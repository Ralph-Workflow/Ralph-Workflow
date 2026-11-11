from __future__ import annotations

import contextlib
import os
import signal
import socket
import sys
import threading
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph.mcp.server import lifecycle
from ralph.mcp.server._mcp_restart_policy import McpRestartPolicy
from ralph.mcp.server._standalone_mcp_process import StandaloneMcpProcess
from ralph.process.manager import SpawnOptions, get_process_manager
from ralph.process.mcp_supervisor import McpSupervisor

if TYPE_CHECKING:
    from ralph.process.manager._managed_process import ManagedProcess

pytestmark = [pytest.mark.subprocess_e2e, pytest.mark.timeout_seconds(10)]


def _reserve_listening_socket() -> tuple[socket.socket, int]:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    sock.listen(128)
    addr: object = sock.getsockname()
    port: int = 0
    if isinstance(addr, tuple) and len(addr) >= 2:
        raw_port = addr[1]
        if isinstance(raw_port, int):
            port = raw_port
    return sock, port


def _spawn_long_sleeper(tmp_path: Path, *, label: str, duration_s: float = 60.0) -> ManagedProcess:
    pm = get_process_manager()
    return pm.spawn(
        [sys.executable, "-c", f"import time; time.sleep({duration_s})"],
        SpawnOptions(cwd=str(tmp_path), label=label),
    )


def _make_session_file(tmp_path: Path, name: str) -> Path:
    session = tmp_path / f"session_{name}.json"
    session.write_text("{}", encoding="utf-8")
    return session


def _make_inner(
    proc: ManagedProcess,
    session_file: Path,
    *,
    endpoint: str = "http://127.0.0.1:9100/mcp",
) -> StandaloneMcpProcess:
    return StandaloneMcpProcess(endpoint=endpoint, process=proc, session_file=session_file)


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return False
    return True


def _reap_managed(proc: ManagedProcess, *, timeout: float = 1.0) -> None:
    if proc.poll() is None:
        with contextlib.suppress(Exception):
            proc.kill()
            proc.wait(timeout=timeout)


def _thread_set_diff(initial: set[int]) -> set[int]:
    current = {t.ident for t in threading.enumerate() if t.ident is not None}
    return current - initial


def _assert_no_leaks(
    *,
    pids: tuple[int, ...],
    sock: socket.socket | None,
    bridge: lifecycle.RestartAwareMcpBridge,
    threads_before: set[int],
) -> None:
    for pid in pids:
        assert not _pid_alive(pid), f"PID {pid} leaked after cleanup"
    assert bridge._listening_socket is None, "Bridge still holds the listening socket"
    if sock is not None:
        with pytest.raises(OSError):
            sock.getsockname()
    leaked = _thread_set_diff(threads_before)
    if leaked:
        names = sorted(t.name for t in threading.enumerate() if t.ident in leaked)
        raise AssertionError(f"Leaked threads: {names}")


def test_e45_real_child_signal_order_sigterm_then_sigkill(tmp_path: Path) -> None:
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
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            if ready_file.exists():
                break
            time.sleep(0.02)
        assert ready_file.exists(), "Child did not become ready in time"

        managed_proc.terminate(grace_period_s=0.2)

        wait_deadline = time.monotonic() + 2.0
        while time.monotonic() < wait_deadline:
            if managed_proc.poll() is not None:
                break
            time.sleep(0.02)

        assert log_file.exists(), "Child log file was not created"
        assert "SIGTERM_RECEIVED" in log_file.read_text(encoding="utf-8")

        assert managed_proc.poll() is not None
        from ralph.process.manager._process_status import ProcessStatus

        assert managed_proc.record.status == ProcessStatus.KILLED

    finally:
        _reap_managed(managed_proc)


class _BlockingVerifyBridge(lifecycle.RestartAwareMcpBridge):
    def __init__(
        self,
        inner: StandaloneMcpProcess,
        *,
        restart_fn: Callable[[], StandaloneMcpProcess],
        restart_policy: McpRestartPolicy,
        run_id: str,
        verify_started: threading.Event,
        verify_release: threading.Event,
        sleep_fn: Callable[[float], object] | None = None,
        workspace_root: Path | None = None,
    ) -> None:
        super().__init__(
            inner,
            restart_fn=restart_fn,
            restart_policy=restart_policy,
            run_id=run_id,
            sleep_fn=sleep_fn,
            workspace_root=workspace_root,
        )
        self._verify_started_ev = verify_started
        self._verify_release_ev = verify_release

    def _verify_candidate(self, candidate_inner: StandaloneMcpProcess) -> bool:
        self._verify_started_ev.set()
        self._verify_release_ev.wait(timeout=2.0)
        return False


def test_e43_supervisor_exit_mid_replacement_reaps_candidate_and_old(tmp_path: Path) -> None:
    sock, _port = _reserve_listening_socket()
    old_proc = _spawn_long_sleeper(tmp_path, label="e43-old")
    cand_proc = _spawn_long_sleeper(tmp_path, label="e43-cand")

    threads_before = {t.ident for t in threading.enumerate() if t.ident is not None}
    old_pid = old_proc.pid
    cand_pid = cand_proc.pid

    try:
        old_inner = _make_inner(old_proc, _make_session_file(tmp_path, "e43-old"))
        cand_inner = _make_inner(cand_proc, _make_session_file(tmp_path, "e43-cand"))

        verify_started = threading.Event()
        verify_release = threading.Event()

        bridge = _BlockingVerifyBridge(
            old_inner,
            restart_fn=lambda: cand_inner,
            restart_policy=McpRestartPolicy(max_restarts=3, cooldown_seconds=0.0),
            run_id=f"test-e43-exit-{uuid.uuid4().hex}",
            sleep_fn=lambda s: None,
            verify_started=verify_started,
            verify_release=verify_release,
        )
        bridge._listening_socket = sock

        supervisor = McpSupervisor(bridge, shutdown_bridge=True)
        supervisor.__enter__()

        def _do_restart() -> None:
            with contextlib.suppress(Exception):
                bridge._perform_restart(process_exited=False, probe_duration_s=0.1)

        restart_thread = threading.Thread(target=_do_restart)
        restart_thread.start()

        assert verify_started.wait(timeout=2.0), "Candidate verify did not start"
        assert _pid_alive(cand_pid), "Candidate died before in-flight window"

        def _do_supervisor_exit() -> None:
            supervisor.__exit__(None, None, None)

        exit_thread = threading.Thread(target=_do_supervisor_exit)
        exit_thread.start()

        verify_release.set()

        restart_thread.join(timeout=2.0)
        exit_thread.join(timeout=2.0)
        assert not restart_thread.is_alive(), "Restart thread did not finish"
        assert not exit_thread.is_alive(), "Supervisor exit thread did not finish"

        assert cand_proc.poll() is not None, "Candidate was not reaped"
        assert old_proc.poll() is not None, "Old child was not reaped"

        _assert_no_leaks(
            pids=(old_pid, cand_pid),
            sock=sock,
            bridge=bridge,
            threads_before=threads_before,
        )

    finally:
        _reap_managed(old_proc)
        _reap_managed(cand_proc)
        with contextlib.suppress(Exception):
            sock.close()


def test_e44_bridge_shutdown_mid_replacement_reaps_candidate_and_old(tmp_path: Path) -> None:
    sock, _port = _reserve_listening_socket()
    old_proc = _spawn_long_sleeper(tmp_path, label="e44-replace-old")
    cand_proc = _spawn_long_sleeper(tmp_path, label="e44-replace-cand")

    threads_before = {t.ident for t in threading.enumerate() if t.ident is not None}
    old_pid = old_proc.pid
    cand_pid = cand_proc.pid

    try:
        old_inner = _make_inner(old_proc, _make_session_file(tmp_path, "e44-replace-old"))
        cand_inner = _make_inner(cand_proc, _make_session_file(tmp_path, "e44-replace-cand"))

        verify_started = threading.Event()
        verify_release = threading.Event()

        bridge = _BlockingVerifyBridge(
            old_inner,
            restart_fn=lambda: cand_inner,
            restart_policy=McpRestartPolicy(max_restarts=3, cooldown_seconds=0.0),
            run_id=f"test-e44-mid-replace-{uuid.uuid4().hex}",
            sleep_fn=lambda s: None,
            verify_started=verify_started,
            verify_release=verify_release,
        )
        bridge._listening_socket = sock

        def _do_restart() -> None:
            with contextlib.suppress(Exception):
                bridge._perform_restart(process_exited=False, probe_duration_s=0.1)

        restart_thread = threading.Thread(target=_do_restart)
        restart_thread.start()

        assert verify_started.wait(timeout=2.0), "Verify did not start"
        assert _pid_alive(cand_pid), "Candidate died before in-flight window"

        bridge.shutdown()

        verify_release.set()
        restart_thread.join(timeout=2.0)
        assert not restart_thread.is_alive(), "Restart thread did not finish"

        assert cand_proc.poll() is not None, "Candidate was not reaped by mid-replacement shutdown"
        assert old_proc.poll() is not None, "Old child was not reaped by shutdown"

        _assert_no_leaks(
            pids=(old_pid, cand_pid),
            sock=sock,
            bridge=bridge,
            threads_before=threads_before,
        )

        bridge.shutdown()

    finally:
        _reap_managed(old_proc)
        _reap_managed(cand_proc)
        with contextlib.suppress(Exception):
            sock.close()


def test_e44_bridge_shutdown_mid_drain_reaps_old_and_candidate(tmp_path: Path) -> None:
    sock, _port = _reserve_listening_socket()
    old_proc = _spawn_long_sleeper(tmp_path, label="e44-drain-old")
    cand_proc = _spawn_long_sleeper(tmp_path, label="e44-drain-cand")

    threads_before = {t.ident for t in threading.enumerate() if t.ident is not None}
    old_pid = old_proc.pid
    cand_pid = cand_proc.pid

    try:
        old_inner = _make_inner(old_proc, _make_session_file(tmp_path, "e44-drain-old"))
        cand_inner = _make_inner(cand_proc, _make_session_file(tmp_path, "e44-drain-cand"))

        bridge = lifecycle.RestartAwareMcpBridge(
            cand_inner,
            restart_fn=lambda: cand_inner,
            restart_policy=McpRestartPolicy(max_restarts=3, cooldown_seconds=0.0),
            run_id=f"test-e44-mid-drain-{uuid.uuid4().hex}",
            workspace_root=tmp_path,
        )
        bridge._listening_socket = sock
        bridge._drain_old(old_inner)
        assert len(bridge._draining_inners) == 1, "old_inner not in draining list"

        bridge.shutdown()

        assert cand_proc.poll() is not None, "Candidate (_inner) was not reaped"
        assert old_proc.poll() is not None, "Old (draining) was not reaped"

        _assert_no_leaks(
            pids=(old_pid, cand_pid),
            sock=sock,
            bridge=bridge,
            threads_before=threads_before,
        )

        bridge.shutdown()

    finally:
        _reap_managed(old_proc)
        _reap_managed(cand_proc)
        with contextlib.suppress(Exception):
            sock.close()


@pytest.mark.timeout_seconds(60)
def test_e45_sigint_during_handover_no_orphans(tmp_path: Path) -> None:
    run_id = f"test-e45-handover-{uuid.uuid4().hex}"
    snap_dir = lifecycle.snapshot_dir(tmp_path, run_id)
    sock, _port = _reserve_listening_socket()
    old_proc = _spawn_long_sleeper(tmp_path, label="e45-handover-old")
    cand_proc = _spawn_long_sleeper(tmp_path, label="e45-handover-cand")

    threads_before = {t.ident for t in threading.enumerate() if t.ident is not None}
    old_pid = old_proc.pid
    cand_pid = cand_proc.pid

    old_inner = _make_inner(old_proc, _make_session_file(tmp_path, "e45-old"))
    cand_inner = _make_inner(
        cand_proc,
        _make_session_file(tmp_path, "e45-cand"),
        endpoint="http://127.0.0.1:9101/mcp",
    )

    trigger_fired = threading.Event()

    def _in_flight_sleep(_seconds: float) -> None:
        if pause_sentinel.exists() and not trigger_fired.is_set():
            trigger_fired.set()
            signal.raise_signal(signal.SIGINT)

    bridge = lifecycle.RestartAwareMcpBridge(
        old_inner,
        restart_fn=lambda: cand_inner,
        restart_policy=McpRestartPolicy(max_restarts=3, cooldown_seconds=0.0),
        run_id=run_id,
        workspace_root=tmp_path,
        sleep_fn=_in_flight_sleep,
        handover_ack_timeout_s=30.0,
    )
    bridge._listening_socket = sock

    pause_sentinel = snap_dir / "accept.pause"

    def _sigint_watchdog() -> None:
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if pause_sentinel.exists():
                if not trigger_fired.is_set():
                    trigger_fired.set()
                    os.kill(os.getpid(), signal.SIGINT)
                return
            time.sleep(0.01)

    watchdog = threading.Thread(target=_sigint_watchdog, name="e45-sigint-watchdog", daemon=True)
    watchdog.start()

    def _raise_keyboard_interrupt(_signum: int, _frame: object) -> None:
        raise KeyboardInterrupt

    previous_sigint_handler = signal.getsignal(signal.SIGINT)
    signal.signal(signal.SIGINT, _raise_keyboard_interrupt)

    interrupted = False
    try:
        bridge._hand_over_accept(old_inner, cand_inner)
    except KeyboardInterrupt:
        interrupted = True
    finally:
        signal.signal(signal.SIGINT, previous_sigint_handler)
        watchdog.join(timeout=1.0)
        bridge.shutdown()

    try:
        assert interrupted, "KeyboardInterrupt did not fire mid-handover"
        assert trigger_fired.is_set(), (
            "in-flight SIGINT trigger neither fired from sleep_fn nor from the watchdog"
        )

        _assert_no_leaks(
            pids=(old_pid, cand_pid),
            sock=sock,
            bridge=bridge,
            threads_before=threads_before,
        )

    finally:
        _reap_managed(old_proc)
        _reap_managed(cand_proc)
        with contextlib.suppress(Exception):
            sock.close()


def test_e51_pid_reuse_pin_no_bare_kill_in_replacement_path() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    lifecycle_py = repo_root / "ralph" / "mcp" / "server" / "lifecycle.py"
    handover_py = repo_root / "ralph" / "mcp" / "server" / "_handover_protocol.py"

    for file_path in (lifecycle_py, handover_py):
        assert file_path.is_file(), f"Expected file {file_path} to exist"
        content = file_path.read_text(encoding="utf-8")
        for line_no, line in enumerate(content.splitlines(), start=1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if "os.kill(" in stripped:
                assert "os.kill(pid, 0)" in stripped or "os.kill(raw_pid, 0)" in stripped, (
                    f"Forbidden bare os.kill signal found in {file_path.name}:{line_no}: {line}"
                )
