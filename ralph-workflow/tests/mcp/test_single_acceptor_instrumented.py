"""Pins E4.3 and E4.4: single acceptor invariant and atomic socket handover.

Catalogue requirement E4.3:
- Single acceptor invariant: No two processes accept on the server endpoint
  simultaneously. The retiring child acknowledges pause and drops the listening
  socket before the candidate resumes accept.
- Unresponsive / SIGSTOPped retiring child: When pause acknowledgement times out,
  the bridge escalates to SIGKILL, confirms the old process is reaped, and only
  then signals the candidate to resume accept.

Catalogue requirement E4.4:
- Atomic socket handover, no fd leak across processes: The parent-owned listening
  socket is cleanly passed to the candidate without leaking fds into unrelated
  children or leaving dangling listeners after shutdown.
"""

from __future__ import annotations

import contextlib
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from ralph.mcp.protocol.env import MCP_INHERITED_FD_ENV
from ralph.mcp.server import lifecycle
from ralph.mcp.server._mcp_restart_policy import McpRestartPolicy
from ralph.mcp.server._snapshots import snapshot_dir
from ralph.mcp.server._standalone_mcp_process import StandaloneMcpProcess

pytestmark = [pytest.mark.subprocess_e2e, pytest.mark.timeout_seconds(10)]


def _is_linux() -> bool:
    return sys.platform.startswith("linux") and Path("/proc").is_dir()


def _get_process_open_inodes(pid: int) -> set[int]:
    """Inspect /proc/<pid>/fd and collect inode numbers of all open file descriptors."""
    fd_dir = Path(f"/proc/{pid}/fd")
    if not fd_dir.is_dir():
        return set()
    inodes: set[int] = set()
    for link in fd_dir.iterdir():
        try:
            inodes.add(link.stat().st_ino)
        except (FileNotFoundError, PermissionError, OSError):
            continue
    return inodes


class _ProcessAdapter:
    """Adapts subprocess.Popen to ProcessLike protocol."""

    def __init__(self, proc: subprocess.Popen[bytes]) -> None:
        self._proc = proc

    @property
    def pid(self) -> int:
        return self._proc.pid

    def poll(self) -> int | None:
        return self._proc.poll()

    def terminate(self, grace_period_s: float = 5.0) -> None:
        self._proc.terminate()

    def wait(self, timeout: float | None = None) -> int | None:
        return self._proc.wait(timeout=timeout)

    def kill(self) -> None:
        self._proc.kill()


def _create_child_script(script_path: Path) -> None:
    code = """\
import os
import signal
import socket
import sys
import time
from pathlib import Path

def main() -> None:
    mode = sys.argv[1]  # "old" or "candidate"
    port = int(sys.argv[2])
    log_file = Path(sys.argv[3])
    pause_file = Path(sys.argv[4]) if len(sys.argv) > 4 and sys.argv[4] != "none" else None
    resume_file = Path(sys.argv[5]) if len(sys.argv) > 5 and sys.argv[5] != "none" else None

    def log(event: str) -> None:
        with log_file.open("a", encoding="utf-8") as f:
            f.write(f"{mode}:{event}:{time.monotonic():.6f}\\n")

    inherited_fd_str = os.environ.get("RALPH_MCP_INHERITED_FD")
    if inherited_fd_str is not None:
        raw_fd = int(inherited_fd_str)
        sock = socket.fromfd(raw_fd, socket.AF_INET, socket.SOCK_STREAM)
    else:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", port))
        sock.listen(128)

    if mode == "candidate":
        log("WAITING_RESUME")
        if resume_file is not None:
            while not resume_file.exists():
                time.sleep(0.01)
        log("STARTED_ACCEPT")
    else:
        log("STARTED_ACCEPT")

    sock.settimeout(0.05)

    while True:
        if mode == "old" and pause_file is not None and pause_file.exists():
            log("PAUSE_DETECTED")
            sock.close()
            if inherited_fd_str is not None:
                try:
                    os.close(int(inherited_fd_str))
                except OSError:
                    pass
            log("DROPPED_SOCKET")
            ack_file = pause_file.with_name(pause_file.name + ".ack")
            ack_file.write_text("ACK\\n", encoding="utf-8")
            log("ACK_WRITTEN")
            while True:
                time.sleep(0.05)

        try:
            conn, _ = sock.accept()
            with conn:
                log("ACCEPTED_CONN")
                conn.sendall(f"PONG_{mode}\\n".encode())
        except (TimeoutError, socket.timeout):
            continue
        except OSError:
            break

if __name__ == "__main__":
    main()
"""
    script_path.write_text(code, encoding="utf-8")


def _read_events(log_file: Path) -> list[tuple[str, str, float]]:
    events: list[tuple[str, str, float]] = []
    if not log_file.exists():
        return events
    for line in log_file.read_text(encoding="utf-8").splitlines():
        parts = line.strip().split(":")
        if len(parts) == 3:
            events.append((parts[0], parts[1], float(parts[2])))
    return events


def _wait_for_mode_event(log_file: Path, mode: str, event_name: str, timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        events = _read_events(log_file)
        if any(m == mode and ev == event_name for m, ev, _ in events):
            return
        time.sleep(0.02)
    raise TimeoutError(f"Timed out waiting for {mode}:{event_name}")


def _spawn_acceptor(
    script_path: Path,
    mode: str,
    port: int,
    log_file: Path,
    parent_fd: int,
    pause_file: Path | None,
    resume_file: Path | None,
) -> subprocess.Popen[bytes]:
    env = dict(os.environ)
    env[str(MCP_INHERITED_FD_ENV)] = str(parent_fd)
    p_arg = str(pause_file) if pause_file is not None else "none"
    r_arg = str(resume_file) if resume_file is not None else "none"
    return subprocess.Popen(
        [sys.executable, str(script_path), mode, str(port), str(log_file), p_arg, r_arg],
        env=env,
        pass_fds=(parent_fd,),
    )


def test_e43_single_acceptor_graceful_handover(tmp_path: Path) -> None:
    """E4.3 & E4.4: Retiring child drops listening socket before candidate resumes accept; no overlap."""
    script_path = tmp_path / "acceptor_child.py"
    _create_child_script(script_path)
    log_file = tmp_path / "events.log"

    parent_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    parent_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    parent_sock.bind(("127.0.0.1", 0))
    parent_sock.listen(128)
    port = parent_sock.getsockname()[1]
    parent_fd = parent_sock.fileno()
    sock_ino = os.fstat(parent_fd).st_ino

    workspace_dir = tmp_path / "ws"
    workspace_dir.mkdir(parents=True, exist_ok=True)
    run_id = "test-single-acceptor-run"
    snap = snapshot_dir(workspace_dir, run_id)
    snap.mkdir(parents=True, exist_ok=True)

    old_stage = "stage_old"
    cand_stage = "stage_cand"
    pause_file = snap / f"accept.pause.{old_stage}"
    resume_file = snap / f"accept.resume.{cand_stage}"

    old_proc = _spawn_acceptor(script_path, "old", port, log_file, parent_fd, pause_file, None)
    cand_proc = _spawn_acceptor(
        script_path, "candidate", port, log_file, parent_fd, None, resume_file
    )

    old_session = tmp_path / "old_session.json"
    old_session.write_text("{}", encoding="utf-8")
    cand_session = tmp_path / "cand_session.json"
    cand_session.write_text("{}", encoding="utf-8")

    old_inner = StandaloneMcpProcess(
        endpoint=f"http://127.0.0.1:{port}/mcp",
        process=_ProcessAdapter(old_proc),
        session_file=old_session,
        stage_id=old_stage,
    )
    cand_inner = StandaloneMcpProcess(
        endpoint=f"http://127.0.0.1:{port}/mcp",
        process=_ProcessAdapter(cand_proc),
        session_file=cand_session,
        stage_id=cand_stage,
    )

    bridge = lifecycle.RestartAwareMcpBridge(
        old_inner,
        restart_fn=lambda: cand_inner,
        restart_policy=McpRestartPolicy(max_restarts=5, cooldown_seconds=0.0),
        run_id=run_id,
        workspace_root=workspace_dir,
    )
    bridge._listening_socket = parent_sock

    try:
        _wait_for_mode_event(log_file, "old", "STARTED_ACCEPT")

        with socket.create_connection(("127.0.0.1", port), timeout=2.0) as conn:
            assert conn.recv(1024) == b"PONG_old\n"

        if _is_linux():
            assert sock_ino in _get_process_open_inodes(old_proc.pid)

        # Perform atomic single-acceptor handover
        bridge._hand_over_accept(old_inner, cand_inner)

        _wait_for_mode_event(log_file, "candidate", "STARTED_ACCEPT")

        with socket.create_connection(("127.0.0.1", port), timeout=2.0) as conn:
            assert conn.recv(1024) == b"PONG_candidate\n"

        events = _read_events(log_file)
        old_dropped_ts = [ts for m, ev, ts in events if m == "old" and ev == "DROPPED_SOCKET"]
        cand_start_ts = [ts for m, ev, ts in events if m == "candidate" and ev == "STARTED_ACCEPT"]

        assert old_dropped_ts, "Old child never logged DROPPED_SOCKET"
        assert cand_start_ts, "Candidate child never logged STARTED_ACCEPT"
        # Invariant E4.3: candidate started accept strictly at or after old child dropped socket
        assert cand_start_ts[0] >= old_dropped_ts[0]

        if _is_linux():
            assert sock_ino not in _get_process_open_inodes(old_proc.pid)
            assert sock_ino in _get_process_open_inodes(cand_proc.pid)

    finally:
        bridge.shutdown()
        for proc in (old_proc, cand_proc):
            with contextlib.suppress(Exception):
                proc.kill()
                proc.wait(timeout=1.0)
        parent_sock.close()


def test_e43_single_acceptor_sigstopped_old_child_kills_before_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """E4.3: When old child is SIGSTOPped and cannot ACK, handover escalates to SIGKILL before candidate resumes."""
    script_path = tmp_path / "acceptor_child.py"
    _create_child_script(script_path)
    log_file = tmp_path / "events_sigstop.log"

    # Use a short handover ACK timeout to keep test fast (<0.5s)
    monkeypatch.setattr(lifecycle, "_HANDOVER_ACK_TIMEOUT_S", 0.2)

    parent_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    parent_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    parent_sock.bind(("127.0.0.1", 0))
    parent_sock.listen(128)
    port = parent_sock.getsockname()[1]
    parent_fd = parent_sock.fileno()

    workspace_dir = tmp_path / "ws_sigstop"
    workspace_dir.mkdir(parents=True, exist_ok=True)
    run_id = "test-sigstop-handover-run"
    snap = snapshot_dir(workspace_dir, run_id)
    snap.mkdir(parents=True, exist_ok=True)

    old_stage = "stage_old_ss"
    cand_stage = "stage_cand_ss"
    pause_file = snap / f"accept.pause.{old_stage}"
    resume_file = snap / f"accept.resume.{cand_stage}"

    old_proc = _spawn_acceptor(script_path, "old", port, log_file, parent_fd, pause_file, None)
    cand_proc = _spawn_acceptor(
        script_path, "candidate", port, log_file, parent_fd, None, resume_file
    )

    old_session = tmp_path / "old_session_ss.json"
    old_session.write_text("{}", encoding="utf-8")
    cand_session = tmp_path / "cand_session_ss.json"
    cand_session.write_text("{}", encoding="utf-8")

    old_inner = StandaloneMcpProcess(
        endpoint=f"http://127.0.0.1:{port}/mcp",
        process=_ProcessAdapter(old_proc),
        session_file=old_session,
        stage_id=old_stage,
    )
    cand_inner = StandaloneMcpProcess(
        endpoint=f"http://127.0.0.1:{port}/mcp",
        process=_ProcessAdapter(cand_proc),
        session_file=cand_session,
        stage_id=cand_stage,
    )

    bridge = lifecycle.RestartAwareMcpBridge(
        old_inner,
        restart_fn=lambda: cand_inner,
        restart_policy=McpRestartPolicy(max_restarts=5, cooldown_seconds=0.0),
        run_id=run_id,
        workspace_root=workspace_dir,
    )
    bridge._listening_socket = parent_sock

    try:
        _wait_for_mode_event(log_file, "old", "STARTED_ACCEPT")

        # Freeze the old child process with SIGSTOP so it cannot ACK pause
        os.kill(old_proc.pid, signal.SIGSTOP)

        # Execute handover: ack timeout will fire, triggering kill escalation
        bridge._hand_over_accept(old_inner, cand_inner)

        # Invariant: Old process MUST be reaped/dead before handover finishes
        assert old_proc.poll() is not None, "Old child was not terminated before candidate resumed!"

        _wait_for_mode_event(log_file, "candidate", "STARTED_ACCEPT")

        with socket.create_connection(("127.0.0.1", port), timeout=2.0) as conn:
            assert conn.recv(1024) == b"PONG_candidate\n"

    finally:
        bridge.shutdown()
        for proc in (old_proc, cand_proc):
            with contextlib.suppress(Exception):
                proc.kill()
                proc.wait(timeout=1.0)
        parent_sock.close()


def test_e44_bridge_shutdown_releases_listening_socket_and_reaps(tmp_path: Path) -> None:
    """E4.4: Bridge shutdown closes listening socket and reaps process; port immediately re-bindable."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    sock.listen(128)
    port = sock.getsockname()[1]

    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(5)"],
    )
    session_file = tmp_path / "session_shutdown.json"
    session_file.write_text("{}", encoding="utf-8")

    inner = StandaloneMcpProcess(
        endpoint=f"http://127.0.0.1:{port}/mcp",
        process=_ProcessAdapter(child),
        session_file=session_file,
    )
    bridge = lifecycle.RestartAwareMcpBridge(
        inner,
        restart_fn=lambda: inner,
        restart_policy=McpRestartPolicy(max_restarts=5, cooldown_seconds=0.0),
        run_id="test-shutdown-run",
    )
    bridge._listening_socket = sock

    try:
        bridge.shutdown()

        # Invariants: child is reaped, socket is closed, port is immediately re-bindable
        assert child.poll() is not None
        assert bridge._listening_socket is None

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as verify_sock:
            verify_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            verify_sock.bind(("127.0.0.1", port))
    finally:
        with contextlib.suppress(Exception):
            child.kill()
            child.wait(timeout=1.0)
        with contextlib.suppress(Exception):
            sock.close()
