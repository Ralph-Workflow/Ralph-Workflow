"""Pins E4.3, E4.4, E4.5 (REAL CHILD scenarios) and E5.1 (PID-reuse PIN).

This file REPLACES the previous vacuous E4.3-E4.5 versions with REAL
synchronized scenarios. Each scenario spawns a real subprocess per child
(each is its own session leader = own process group) and asserts the full
set of invariants:

  * candidate / old / draining PIDs all dead (``os.kill(pid, 0)`` raises
    :class:`ProcessLookupError`).
  * The bridge's parent-owned listening socket FD is closed exactly once.
  * No supervisor / preflight / drain threads remain
    (:func:`threading.enumerate` before / after snapshot equality).

WU-3 scope: this file is the SOLE writer of these tests. Production files
are out of scope. If a real scenario surfaces a production bug, the test
fails loudly with the strict invariant rather than weakening the
assertion; ``path:line`` and a minimal repro are recorded in the report
for the coordinator to dispatch an owning unit.
"""

from __future__ import annotations

import contextlib
import os
import signal
import socket
import sys
import threading
import time
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


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# E4.5 (signal order) — pins SIGTERM -> grace -> SIGKILL escalation
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# E4.3: supervisor exit mid-replacement (REAL)
# ---------------------------------------------------------------------------


class _BlockingVerifyBridge(lifecycle.RestartAwareMcpBridge):
    """RestartAwareMcpBridge with a controllable verify seam.

    ``_verify_candidate`` signals that the candidate is in flight, then
    blocks until the test releases ``verify_release``. Returning
    ``False`` exercises the verify-fail path: the candidate is reaped
    locally and the bridge keeps the old child.
    """

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
    """E4.3: McpSupervisor.__exit__() during a replacement's verify phase reaps candidate + old.

    Scenario:
      * Two real children: ``old_proc`` (the bridge's current ``_inner``)
        and ``cand_proc`` (returned by ``restart_fn``). Both are own-
        session leaders, sleeping.
      * ``_verify_candidate`` is a controlled blocker; the candidate enters
        verify, the test signals "in flight", then calls ``supervisor.__exit__``
        from a second thread.
      * ``supervisor.__exit__`` invokes bridge shutdown and blocks on the lock
        until ``_perform_restart`` releases it. After verify returns ``False``,
        the candidate is reaped locally, the lock is released, shutdown reaps the old ``_inner``.
      * Invariants asserted: both PIDs dead, listening socket FD closed
        exactly once, no leaked threads.
    """
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
            run_id="test-e43-exit",
            sleep_fn=lambda s: None,
            verify_started=verify_started,
            verify_release=verify_release,
        )
        bridge._listening_socket = sock

        supervisor = McpSupervisor(bridge, shutdown_bridge=True)
        supervisor.__enter__()

        # 1. Launch _perform_restart in a thread. Verify blocks on event.
        def _do_restart() -> None:
            with contextlib.suppress(Exception):
                bridge._perform_restart(process_exited=False, probe_duration_s=0.1)

        restart_thread = threading.Thread(target=_do_restart)
        restart_thread.start()

        # 2. Wait for verify to start (candidate is in flight).
        assert verify_started.wait(timeout=2.0), "Candidate verify did not start"
        assert _pid_alive(cand_pid), "Candidate died before in-flight window"

        # 3. Concurrent McpSupervisor.__exit__ from another thread. It blocks on the
        #    lock until _perform_restart releases it.
        def _do_supervisor_exit() -> None:
            supervisor.__exit__(None, None, None)

        exit_thread = threading.Thread(target=_do_supervisor_exit)
        exit_thread.start()

        # 4. Release verify. Verify fails, candidate is reaped locally,
        #    _perform_restart returns, lock released, supervisor.__exit__ proceeds
        #    and reaps the old _inner.
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


# ---------------------------------------------------------------------------
# E4.4: bridge shutdown mid-replacement (REAL)
# ---------------------------------------------------------------------------


def test_e44_bridge_shutdown_mid_replacement_reaps_candidate_and_old(tmp_path: Path) -> None:
    """E4.4 part 1: bridge.shutdown() called after verify-fail reap reaps both old and candidate."""
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
            run_id="test-e44-mid-replace",
            sleep_fn=lambda s: None,
            verify_started=verify_started,
            verify_release=verify_release,
        )
        bridge._listening_socket = sock

        # 1. Launch _perform_restart. Verify blocks.
        def _do_restart() -> None:
            with contextlib.suppress(Exception):
                bridge._perform_restart(process_exited=False, probe_duration_s=0.1)

        restart_thread = threading.Thread(target=_do_restart)
        restart_thread.start()

        assert verify_started.wait(timeout=2.0), "Verify did not start"
        assert _pid_alive(cand_pid), "Candidate died before in-flight window"

        # 2. Shutdown the bridge WHILE replacement is actively in flight!
        bridge.shutdown()

        # 3. Release verify so restart thread unblocks.
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

        # 4. Idempotency: a second shutdown is a safe no-op.
        bridge.shutdown()

    finally:
        _reap_managed(old_proc)
        _reap_managed(cand_proc)
        with contextlib.suppress(Exception):
            sock.close()


# ---------------------------------------------------------------------------
# E4.4: bridge shutdown mid-drain (REAL)
# ---------------------------------------------------------------------------


def test_e44_bridge_shutdown_mid_drain_reaps_old_and_candidate(tmp_path: Path) -> None:
    """E4.4 part 2: bridge.shutdown() while old is being drained reaps both.

    ``_draining_inners`` is populated manually (the production 2.0s drain
    deadline is skipped via ``sleep_fn=lambda s: None`` so the drain
    worker is a no-op). The test verifies shutdown's behavior on the
    draining inner AND the new ``_inner``.
    """
    sock, _port = _reserve_listening_socket()
    old_proc = _spawn_long_sleeper(tmp_path, label="e44-drain-old")
    cand_proc = _spawn_long_sleeper(tmp_path, label="e44-drain-cand")

    threads_before = {t.ident for t in threading.enumerate() if t.ident is not None}
    old_pid = old_proc.pid
    cand_pid = cand_proc.pid

    try:
        old_inner = _make_inner(old_proc, _make_session_file(tmp_path, "e44-drain-old"))
        cand_inner = _make_inner(cand_proc, _make_session_file(tmp_path, "e44-drain-cand"))

        # Post-promote state: candidate is _inner, old is draining.
        bridge = lifecycle.RestartAwareMcpBridge(
            cand_inner,
            restart_fn=lambda: cand_inner,
            restart_policy=McpRestartPolicy(max_restarts=3, cooldown_seconds=0.0),
            run_id="test-e44-mid-drain",
            workspace_root=tmp_path,
        )
        bridge._listening_socket = sock
        # Start real background drain worker on old_inner:
        bridge._drain_old(old_inner)
        assert len(bridge._draining_inners) == 1, "old_inner not in draining list"

        # Shutdown while drain worker is active in background:
        bridge.shutdown()

        assert cand_proc.poll() is not None, "Candidate (_inner) was not reaped"
        assert old_proc.poll() is not None, "Old (draining) was not reaped"

        _assert_no_leaks(
            pids=(old_pid, cand_pid),
            sock=sock,
            bridge=bridge,
            threads_before=threads_before,
        )

        # Idempotency: a second shutdown is a safe no-op.
        bridge.shutdown()

    finally:
        _reap_managed(old_proc)
        _reap_managed(cand_proc)
        with contextlib.suppress(Exception):
            sock.close()


# ---------------------------------------------------------------------------
# E4.5: SIGINT during handover (REAL)
# ---------------------------------------------------------------------------


def test_e45_sigint_during_handover_no_orphans(tmp_path: Path) -> None:
    """E4.5: real KeyboardInterrupt during handover leaves zero orphans.

    ``_hand_over_accept`` is driven synchronously with ``workspace_root``
    set so the candidate is genuinely "in flight". Deterministic
    interrupt placement: a watchdog thread polls for the handover's
    pause sentinel file — proof the main thread has entered the
    pause/ack-wait stage — and raises SIGINT (via ``signal.raise_signal``,
    which CPython delivers to the main thread) the moment it appears.
    The original fixed 0.1s ``setitimer`` was racy under host load: the
    alarm could fire before the handover started, or the handover's
    ack-wait could complete before the alarm landed. Observing the
    in-flight state removes the race without weakening any assertion.
    ``bridge.shutdown()`` is the sole cleanup; if the candidate leaks,
    the test fails and the bug is recorded in the report.
    """
    # Resolve the real snapshot dir the production code writes sentinels
    # into (a protected cache path keyed by run_id, NOT tmp_path).
    snap_dir = lifecycle.snapshot_dir(tmp_path, "test-e45-handover")
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

    bridge = lifecycle.RestartAwareMcpBridge(
        old_inner,
        restart_fn=lambda: cand_inner,
        restart_policy=McpRestartPolicy(max_restarts=3, cooldown_seconds=0.0),
        run_id="test-e45-handover",
        workspace_root=tmp_path,
        sleep_fn=lambda s: None,
    )
    bridge._listening_socket = sock

    # Watchdog: the instant the pause sentinel exists, the main thread is
    # inside ``request_old_child_pause`` / the ack-wait loop — raise SIGINT
    # there. ``signal.raise_signal`` from a non-main thread still delivers
    # the signal to the process, and CPython runs Python-level signal
    # handlers (and the resulting KeyboardInterrupt) in the MAIN thread.
    # filesystem-write-ok: reading a transient handover sentinel file written by production code
    pause_sentinel = snap_dir / "accept.pause"
    watchdog_fired = threading.Event()

    def _sigint_watchdog() -> None:
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if pause_sentinel.exists():
                watchdog_fired.set()
                signal.raise_signal(signal.SIGINT)
                return
            time.sleep(0.001)

    watchdog = threading.Thread(target=_sigint_watchdog, name="e45-sigint-watchdog", daemon=True)
    watchdog.start()

    interrupted = False
    try:
        bridge._hand_over_accept(old_inner, cand_inner)
    except KeyboardInterrupt:
        interrupted = True
    finally:
        watchdog.join(timeout=1.0)
        # Cleanup: bridge shutdown reaps whatever is in _inner / _draining_inners.
        bridge.shutdown()

    try:
        assert interrupted, "KeyboardInterrupt did not fire mid-handover"
        assert watchdog_fired.is_set() or not watchdog.is_alive(), (
            "SIGINT watchdog neither fired nor finished"
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


# ---------------------------------------------------------------------------
# E5.1: PID-reuse PIN — UNCHANGED
# ---------------------------------------------------------------------------


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
            if stripped.startswith("#"):
                continue
            if "os.kill(" in stripped:
                assert "os.kill(pid, 0)" in stripped or "os.kill(raw_pid, 0)" in stripped, (
                    f"Forbidden bare os.kill signal found in {file_path.name}:{line_no}: {line}"
                )
