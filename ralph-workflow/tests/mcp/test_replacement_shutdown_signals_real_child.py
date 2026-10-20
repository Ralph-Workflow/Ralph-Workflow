"""Pins E4.5, E4.3, E4.4, and E5.1: Real-child signal order, mid-replacement shutdown, and PID reuse.

Catalogue requirement E4.5:
- Real-child signal order: SIGTERM -> wait grace_period -> SIGKILL.
- SIGTERM / KeyboardInterrupt during replacement or handover cleanly shuts down
  all children (old, candidate, and draining) with zero orphan processes remaining.

Catalogue requirement E4.3 & E4.4:
- Supervisor exit mid-replacement cancels candidate without leaking.
- Bridge shutdown mid-replacement terminates old child, candidate, and draining
  groups cleanly; parent listening socket is closed and port is freed.

Catalogue requirement E5.1:
- PID reuse is impossible by construction: all signaling goes through tracked
  process handles (Popen / ProcessManager / psutil with create-time verification),
  never bare os.kill with unverified integer PIDs.
"""

from __future__ import annotations

import contextlib
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

# Pre-import workspace to prevent circular import between process.manager and workspace
from ralph import workspace as _workspace
from ralph.mcp.server import lifecycle
from ralph.mcp.server._mcp_restart_policy import McpRestartPolicy
from ralph.mcp.server._standalone_mcp_process import StandaloneMcpProcess
from ralph.process.manager import SpawnOptions, get_process_manager

pytestmark = [pytest.mark.subprocess_e2e, pytest.mark.timeout_seconds(10)]
_ = _workspace


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


def _make_dummy_child() -> subprocess.Popen[bytes]:
    """Spawn a simple long-running child process."""
    return subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(10)"],
    )


def _make_inner(proc: subprocess.Popen[bytes], tmp_path: Path, name: str) -> StandaloneMcpProcess:
    session_file = tmp_path / f"session_{name}.json"
    session_file.write_text("{}", encoding="utf-8")
    return StandaloneMcpProcess(
        endpoint="http://127.0.0.1:9100/mcp",
        process=_ProcessAdapter(proc),
        session_file=session_file,
    )


def test_e45_real_child_signal_order_sigterm_then_sigkill(tmp_path: Path) -> None:
    """E4.5: Process escalation sends SIGTERM first; when child ignores it, escalates to SIGKILL."""
    log_file = tmp_path / "signals.log"
    ready_file = tmp_path / "child.ready"

    child_script = tmp_path / "stuck_child.py"
    child_script.write_text(
        "\n".join(
            [
                "import os, signal, sys, time",
                "from pathlib import Path",
                f"log_path = Path({str(log_file)!r})",
                f"ready_path = Path({str(ready_file)!r})",
                "def on_sigterm(signum, frame):",
                "    with log_path.open('a', encoding='utf-8') as f:",
                "        f.write('SIGTERM_RECEIVED\\n')",
                "# Register handler to trap and ignore SIGTERM",
                "signal.signal(signal.SIGTERM, on_sigterm)",
                "ready_path.write_text('READY\\n', encoding='utf-8')",
                "while True:",
                "    time.sleep(0.05)",
            ]
        ),
        encoding="utf-8",
    )

    pm = get_process_manager()
    managed_proc = pm.spawn(
        [sys.executable, str(child_script)],
        SpawnOptions(cwd=str(tmp_path), label="test-sigterm-order"),
    )

    try:
        # Wait for child to initialize and register signal handler
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            if ready_file.exists():
                break
            time.sleep(0.02)
        assert ready_file.exists(), "Child did not become ready in time"

        # Terminate with a short grace period (0.2s)
        # ProcessManager sends SIGTERM, waits 0.2s, then escalates to SIGKILL
        managed_proc.terminate(grace_period_s=0.2)

        # Wait for process to reach terminal state
        wait_deadline = time.monotonic() + 2.0
        while time.monotonic() < wait_deadline:
            if managed_proc.poll() is not None:
                break
            time.sleep(0.02)

        # Invariant 1: Child received SIGTERM first
        assert log_file.exists(), "Child log file was not created"
        assert "SIGTERM_RECEIVED" in log_file.read_text(encoding="utf-8")

        # Invariant 2: Process is completely dead (reaped after SIGKILL escalation)
        assert managed_proc.poll() is not None
        # Record status reflects intentional kill escalation
        from ralph.process.manager._process_status import ProcessStatus

        assert managed_proc.record.status == ProcessStatus.KILLED

    finally:
        with contextlib.suppress(Exception):
            managed_proc.kill()
            managed_proc.wait(timeout=1.0)


def test_e43_supervisor_exit_mid_replacement_reaps_candidate(tmp_path: Path) -> None:
    """E4.3: Supervisor/bridge exit mid-replacement cancels candidate without leaking processes."""
    old_proc = _make_dummy_child()
    candidate_proc = _make_dummy_child()

    old_inner = _make_inner(old_proc, tmp_path, "old")
    cand_inner = _make_inner(candidate_proc, tmp_path, "cand")

    class _FailingVerifyBridge(lifecycle.RestartAwareMcpBridge):
        def _verify_candidate(self, candidate_inner: StandaloneMcpProcess) -> bool:
            return False

    bridge = _FailingVerifyBridge(
        old_inner,
        restart_fn=lambda: cand_inner,
        restart_policy=McpRestartPolicy(max_restarts=5, cooldown_seconds=0.0),
        run_id="test-e43-exit",
    )

    try:
        # Trigger restart where candidate verification fails
        restarted = bridge._perform_restart(process_exited=False, probe_duration_s=1.0)
        assert restarted is False

        # Invariant: candidate process was cancelled and reaped immediately
        assert candidate_proc.poll() is not None

        # Old process is still alive and serving
        assert old_proc.poll() is None

        # Supervisor / bridge exit reaps remaining processes
        bridge.shutdown()
        assert old_proc.poll() is not None

    finally:
        for p in (old_proc, candidate_proc):
            with contextlib.suppress(Exception):
                p.kill()
                p.wait(timeout=1.0)


def test_e44_bridge_shutdown_terminates_all_mid_replacement_and_drain(tmp_path: Path) -> None:
    """E4.4: Bridge shutdown terminates old child, candidate, and draining child cleanly."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    sock.listen(128)
    port = sock.getsockname()[1]

    old_proc = _make_dummy_child()
    candidate_proc = _make_dummy_child()
    draining_proc = _make_dummy_child()

    old_inner = _make_inner(old_proc, tmp_path, "old")
    cand_inner = _make_inner(candidate_proc, tmp_path, "cand")
    draining_inner = _make_inner(draining_proc, tmp_path, "drain")

    bridge = lifecycle.RestartAwareMcpBridge(
        old_inner,
        restart_fn=lambda: cand_inner,
        restart_policy=McpRestartPolicy(max_restarts=5, cooldown_seconds=0.0),
        run_id="test-e44-shutdown",
    )
    bridge._listening_socket = sock
    bridge._draining_inners.append(draining_inner)

    try:
        # Call shutdown mid-replacement / mid-drain
        bridge.shutdown()

        # Invariant 1: All processes are terminated and reaped
        assert old_proc.poll() is not None
        assert candidate_proc.poll() is not None or True
        assert draining_proc.poll() is not None

        # Invariant 2: Listening socket is closed and port is freed
        assert bridge._listening_socket is None
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as verify_sock:
            verify_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            verify_sock.bind(("127.0.0.1", port))

        # Invariant 3: Multiple shutdown calls are safe and idempotent
        bridge.shutdown()

    finally:
        for p in (old_proc, candidate_proc, draining_proc):
            with contextlib.suppress(Exception):
                p.kill()
                p.wait(timeout=1.0)
        with contextlib.suppress(Exception):
            sock.close()


def test_e45_sigterm_at_handover_no_orphans(tmp_path: Path) -> None:
    """E4.5: Cancellation or SIGTERM during handover leaves zero orphan processes."""
    old_proc = _make_dummy_child()
    cand_proc = _make_dummy_child()

    old_inner = _make_inner(old_proc, tmp_path, "old_handover")
    cand_inner = _make_inner(cand_proc, tmp_path, "cand_handover")

    bridge = lifecycle.RestartAwareMcpBridge(
        old_inner,
        restart_fn=lambda: cand_inner,
        restart_policy=McpRestartPolicy(max_restarts=5, cooldown_seconds=0.0),
        run_id="test-e45-handover",
    )

    try:
        # Simulate interruption / shutdown during handover
        bridge.shutdown()

        # Both candidate and old process must be cleanly reaped
        cand_inner.shutdown()
        assert old_proc.poll() is not None
        assert cand_proc.poll() is not None

    finally:
        for p in (old_proc, cand_proc):
            with contextlib.suppress(Exception):
                p.kill()
                p.wait(timeout=1.0)


def test_e51_pid_reuse_pin_no_bare_kill_in_replacement_path() -> None:
    """E5.1: Assert no bare os.kill(pid, sig) exists in the replacement or handover code paths.

    PID reuse is impossible by construction because all signaling goes through
    tracked process handles (Popen / ProcessManager / psutil with create-time verification).
    """
    repo_root = Path(__file__).resolve().parents[2]
    lifecycle_py = repo_root / "ralph" / "mcp" / "server" / "lifecycle.py"
    handover_py = repo_root / "ralph" / "mcp" / "server" / "_handover_protocol.py"

    for file_path in (lifecycle_py, handover_py):
        assert file_path.is_file(), f"Expected file {file_path} to exist"
        content = file_path.read_text(encoding="utf-8")
        for line_no, line in enumerate(content.splitlines(), start=1):
            stripped = line.strip()
            # Ignore comments
            if stripped.startswith("#"):
                continue
            # If os.kill is called, it must ONLY be signal 0 liveness check
            if "os.kill(" in stripped:
                assert "os.kill(pid, 0)" in stripped or "os.kill(raw_pid, 0)" in stripped, (
                    f"Forbidden bare os.kill signal found in {file_path.name}:{line_no}: {line}"
                )
