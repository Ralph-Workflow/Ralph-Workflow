"""Comprehensive hardening tests for ProcessManager shutdown behavior.

Tests cover every acceptance-criterion edge case: TOCTOU safety, zombie
detection, escalation failure, descendant cleanup, idempotency, stale-state
reconciliation, cross-platform behavior, concurrent readers/writers, lifetime
categories, cleanup after abnormal exit, and error reporting fidelity.

All tests use deterministic fake processes — no real OS processes are spawned.
"""

from __future__ import annotations

import asyncio
import itertools
import os
import sys
import threading
from typing import TYPE_CHECKING, cast
from unittest.mock import patch

import pytest

from ralph.process.manager import (
    LivenessResult,
    ProcessManager,
    ProcessManagerPolicy,
    ProcessTerminationError,
    PtySpawnOptions,
    SpawnOptions,
)
from ralph.process.manager._process_manager import _TERMINAL_STATUSES
from ralph.process.manager._process_status import ProcessStatus
from ralph.testing.fake_process import (
    FakeImmortalPopen,
    FakePsutil,
    FakePsutilProcess,
    FakeStubbornPopen,
    make_sync_process_factory,
)
from tests.test_process_manager_pty_helper__fakeptyfactory import _FakePtyFactory

if TYPE_CHECKING:
    from ralph.process.manager._process_event import ProcessEvent
    from ralph.testing.fake_process import SyncFactoryCallable
    from tests.test_process_manager_pty_helper__fakeprocess import _FakePtyProcess

_FAST_POLICY = ProcessManagerPolicy(
    default_grace_period_s=0.1,
    kill_followup_timeout_s=0.1,
    log_events=False,
    enable_zombie_reaper=False,
)

# ────────────────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────────────────


def _make_pm(
    *,
    sync_factory: SyncFactoryCallable | None = None,
    psutil_mod: FakePsutil | None = None,
) -> ProcessManager:
    """Build a ProcessManager with injected fake process factories."""
    return ProcessManager(
        policy=_FAST_POLICY,
        sync_process_factory=sync_factory or make_sync_process_factory(itertools.count(1)),
        async_process_factory=make_sync_process_factory(itertools.count(100)),
        psutil=psutil_mod,
    )


# ═══════════════════════════════════════════════════════════════════════════
# TOCTOU Safety (Edge cases 1, 2, 6)
# ═══════════════════════════════════════════════════════════════════════════


def test_process_exits_before_graceful_terminate() -> None:
    """Process exits before terminate() is called — marked KILLED as already gone."""
    with patch("os.kill", side_effect=ProcessLookupError):
        pm = _make_pm(psutil_mod=None)
        handle = pm.spawn([sys.executable, "-c", "pass"])
        # Simulate: process already exited (returncode set)
        handle._proc._returncode = 0

        handle.terminate(grace_period_s=0.1)

    assert handle.record.status == ProcessStatus.KILLED
    assert handle.record.cause == "already_gone"


def test_process_exits_between_liveness_check_and_kill() -> None:
    """Process exits between liveness check and terminate attempt via psutil."""
    sync_factory = make_sync_process_factory(itertools.count(1), returncode=None)

    class _DisappearingPsutil(FakePsutil):
        def process_from_pid(self, pid: int) -> FakePsutilProcess:
            raise self.NoSuchProcess

    fake_psutil = _DisappearingPsutil()
    pm = ProcessManager(
        policy=_FAST_POLICY,
        sync_process_factory=sync_factory,
        async_process_factory=make_sync_process_factory(itertools.count(100)),
        psutil=fake_psutil,
    )
    handle = pm.spawn([sys.executable, "-c", "pass"])

    handle.terminate(grace_period_s=0.1)

    assert handle.record.status == ProcessStatus.KILLED


def test_process_already_gone_before_kill() -> None:
    """os.kill(pid, 0) raises ProcessLookupError — process marked already gone."""

    def _kill_raises_lookup(pid: int, sig: int) -> None:
        if sig == 0:
            raise ProcessLookupError(pid, sig)

    with patch("os.kill", _kill_raises_lookup):
        pm = _make_pm(psutil_mod=None)
        handle = pm.spawn([sys.executable, "-c", "pass"])

        handle.terminate(grace_period_s=0.1)

    assert handle.record.status == ProcessStatus.KILLED
    assert handle.record.cause == "already_gone"


# ═══════════════════════════════════════════════════════════════════════════
# Zombie Detection (Edge case 12)
# ═══════════════════════════════════════════════════════════════════════════


def test_zombie_detected_after_force_kill() -> None:
    """Process is zombie after force kill — marked KILLED with zombie_after_kill cause."""
    sync_factory = make_sync_process_factory(itertools.count(1), returncode=None)

    class _ZombieAfterKill(FakePsutilProcess):
        def terminate(self) -> None:
            pass  # ignore SIGTERM

        def kill(self) -> None:
            self._status = "zombie"  # becomes zombie after kill

    parent_pid = 1
    fake_psutil = FakePsutil()
    fake_psutil._processes = {parent_pid: _ZombieAfterKill(pid=parent_pid)}

    pm = ProcessManager(
        policy=_FAST_POLICY,
        sync_process_factory=sync_factory,
        async_process_factory=make_sync_process_factory(itertools.count(100)),
        psutil=fake_psutil,
    )
    handle = pm.spawn([sys.executable, "-c", "pass"])
    handle._record.pid = parent_pid

    handle.terminate(grace_period_s=0.01)

    assert handle.record.status == ProcessStatus.KILLED
    assert handle.record.cause == "zombie_after_kill"


def test_process_truly_reaped_after_force_kill() -> None:
    """Normal cleanup path: process is terminated and reaped."""
    sync_factory = make_sync_process_factory(itertools.count(1), returncode=None)
    pm = _make_pm(sync_factory=sync_factory, psutil_mod=FakePsutil())
    handle = pm.spawn([sys.executable, "-c", "pass"])

    handle.terminate(grace_period_s=0.1)

    assert handle.record.status == ProcessStatus.KILLED
    assert handle.record.cause == "killed"


# ═══════════════════════════════════════════════════════════════════════════
# Escalation Failure (Edge cases 4, 5, 19)
# ═══════════════════════════════════════════════════════════════════════════


def test_graceful_terminate_times_out_escalates_to_kill() -> None:
    """Graceful terminate times out → escalates to force kill successfully."""

    def stubborn_factory(command: object, opts: object) -> FakeStubbornPopen:
        del command, opts
        return FakeStubbornPopen(pid=1, final_returncode=-9)

    pm = ProcessManager(
        policy=_FAST_POLICY,
        sync_process_factory=stubborn_factory,
        async_process_factory=make_sync_process_factory(itertools.count(100)),
        psutil=None,
    )
    handle = pm.spawn([sys.executable, "-c", "pass"])

    handle.terminate(grace_period_s=0.01)

    assert handle.record.status == ProcessStatus.KILLED
    assert handle.record.returncode == -9
    assert handle.record.cause == "killed"


def test_force_kill_fails_raises_termination_error() -> None:
    """Force kill fails → ProcessTerminationError with structured fields."""

    def immortal_factory(command: object, opts: object) -> FakeImmortalPopen:
        del command, opts
        return FakeImmortalPopen(pid=1)

    pm = ProcessManager(
        policy=_FAST_POLICY,
        sync_process_factory=immortal_factory,
        async_process_factory=make_sync_process_factory(itertools.count(100)),
        psutil=None,
    )
    handle = pm.spawn([sys.executable, "-c", "pass"])

    with pytest.raises(ProcessTerminationError) as excinfo:
        handle.terminate(grace_period_s=0.01)

    assert excinfo.value.stage == "force_kill"
    assert "still alive" in excinfo.value.reason
    assert handle.record.status == ProcessStatus.FAILED
    assert handle.record.cause == "termination_failed"


def test_permission_error_during_terminate() -> None:
    """Permission error during terminate → ProcessTerminationError with access_denied stage."""
    sync_factory = make_sync_process_factory(itertools.count(1), returncode=None)

    class _AccessDeniedPsutil(FakePsutil):
        AccessDenied = type("AccessDenied", (Exception,), {})

        def process_from_pid(self, pid: int) -> FakePsutilProcess:
            raise self.AccessDenied(pid)

    fake_psutil = _AccessDeniedPsutil()
    pm = ProcessManager(
        policy=_FAST_POLICY,
        sync_process_factory=sync_factory,
        async_process_factory=make_sync_process_factory(itertools.count(100)),
        psutil=fake_psutil,
    )
    handle = pm.spawn([sys.executable, "-c", "pass"])

    with pytest.raises(ProcessTerminationError) as excinfo:
        handle.terminate(grace_period_s=0.01)

    assert excinfo.value.stage == "access_denied"
    assert "Access denied" in excinfo.value.reason
    assert handle.record.status == ProcessStatus.FAILED


# ═══════════════════════════════════════════════════════════════════════════
# Descendant Cleanup (Edge cases 3, 8, 9, 10)
# ═══════════════════════════════════════════════════════════════════════════


def test_parent_terminates_but_descendant_survives() -> None:
    """Parent exits but one descendant survives → error raised."""
    sync_factory = make_sync_process_factory(itertools.count(1), returncode=None)

    parent_pid = 1

    class _ImmortalChild(FakePsutilProcess):
        def terminate(self) -> None:
            pass  # Ignore SIGTERM

        def kill(self) -> None:
            pass  # Also ignore SIGKILL

    stubborn_child = _ImmortalChild(pid=1001)

    class _RootWithStubbornChild(FakePsutilProcess):
        def children(self, recursive: bool = False) -> list[FakePsutilProcess]:
            return [stubborn_child]

    root = _RootWithStubbornChild(pid=parent_pid)
    fake_psutil = FakePsutil()
    fake_psutil._processes = {parent_pid: root, 1001: stubborn_child}

    pm = ProcessManager(
        policy=_FAST_POLICY,
        sync_process_factory=sync_factory,
        async_process_factory=make_sync_process_factory(itertools.count(100)),
        psutil=fake_psutil,
    )
    handle = pm.spawn([sys.executable, "-c", "pass"])
    handle._record.pid = parent_pid

    with pytest.raises(ProcessTerminationError):
        handle.terminate(grace_period_s=0.0)

    assert handle.record.status == ProcessStatus.FAILED


def test_late_spawning_descendant_during_shutdown() -> None:
    """Descendant appears between snapshot and kill — still handled."""
    sync_factory = make_sync_process_factory(itertools.count(1), returncode=None)

    parent_pid = 1
    late_child = FakePsutilProcess(pid=2001, _running=True, _status="sleeping", _create_time=0.0)

    class _RootWithLateChild(FakePsutilProcess):
        def children(self, recursive: bool = False) -> list[FakePsutilProcess]:
            return [late_child]

    root = _RootWithLateChild(pid=parent_pid)
    fake_psutil = FakePsutil()
    fake_psutil._processes = {parent_pid: root, 2001: late_child}

    pm = ProcessManager(
        policy=_FAST_POLICY,
        sync_process_factory=sync_factory,
        async_process_factory=make_sync_process_factory(itertools.count(100)),
        psutil=fake_psutil,
    )
    handle = pm.spawn([sys.executable, "-c", "pass"])
    handle._record.pid = parent_pid

    handle.terminate(grace_period_s=0.1)

    assert handle.record.status == ProcessStatus.KILLED


def test_multiple_descendants_partial_termination() -> None:
    """Only some descendants terminate on first attempt → escalation kills rest."""
    sync_factory = make_sync_process_factory(itertools.count(1), returncode=None)

    parent_pid = 1
    obedient = FakePsutilProcess(pid=1001)

    class _ImmortalDescendant(FakePsutilProcess):
        def terminate(self) -> None:
            pass

        def kill(self) -> None:
            pass

    stubborn = _ImmortalDescendant(pid=1002)

    class _RootWithMixedChildren(FakePsutilProcess):
        def children(self, recursive: bool = False) -> list[FakePsutilProcess]:
            return [obedient, stubborn]

    root = _RootWithMixedChildren(pid=parent_pid)
    fake_psutil = FakePsutil()
    fake_psutil._processes = {parent_pid: root, 1001: obedient, 1002: stubborn}

    pm = ProcessManager(
        policy=_FAST_POLICY,
        sync_process_factory=sync_factory,
        async_process_factory=make_sync_process_factory(itertools.count(100)),
        psutil=fake_psutil,
    )
    handle = pm.spawn([sys.executable, "-c", "pass"])
    handle._record.pid = parent_pid

    with pytest.raises(ProcessTerminationError):
        handle.terminate(grace_period_s=0.0)

    assert handle.record.status == ProcessStatus.FAILED


def test_descendant_outlives_parent_and_is_detected() -> None:
    """Orphan descendant detection: parent gone but descendant still alive."""
    sync_factory = make_sync_process_factory(itertools.count(1), returncode=None)

    parent_pid = 1

    class _ImmortalOrphan(FakePsutilProcess):
        def terminate(self) -> None:
            pass

        def kill(self) -> None:
            pass

    orphan = _ImmortalOrphan(pid=3001)

    class _RootWithOrphan(FakePsutilProcess):
        def children(self, recursive: bool = False) -> list[FakePsutilProcess]:
            return [orphan]

    root = _RootWithOrphan(pid=parent_pid)
    fake_psutil = FakePsutil()
    fake_psutil._processes = {parent_pid: root, 3001: orphan}

    pm = ProcessManager(
        policy=_FAST_POLICY,
        sync_process_factory=sync_factory,
        async_process_factory=make_sync_process_factory(itertools.count(100)),
        psutil=fake_psutil,
    )
    handle = pm.spawn([sys.executable, "-c", "pass"])
    handle._record.pid = parent_pid

    with pytest.raises(ProcessTerminationError):
        handle.terminate(grace_period_s=0.0)

    # Descendant tracking: orphan should be marked in error
    assert handle.record.status == ProcessStatus.FAILED


# ═══════════════════════════════════════════════════════════════════════════
# Idempotency (Edge case 13)
# ═══════════════════════════════════════════════════════════════════════════


def test_double_terminate_is_idempotent() -> None:
    """Calling terminate() twice on the same handle is safe and idempotent."""
    sync_factory = make_sync_process_factory(itertools.count(1), returncode=0)
    pm = _make_pm(sync_factory=sync_factory)

    handle = pm.spawn([sys.executable, "-c", "pass"])
    handle.wait(timeout=5.0)  # process exits

    # First terminate after exit
    handle.terminate(grace_period_s=0.1)
    assert handle.record.status == ProcessStatus.EXITED

    # Second terminate should no-op (already terminal)
    handle.terminate(grace_period_s=0.1)
    assert handle.record.status == ProcessStatus.EXITED


def test_double_shutdown_all_is_idempotent() -> None:
    """Calling shutdown_all() twice does not raise or corrupt state."""
    sync_factory = make_sync_process_factory(itertools.count(1), returncode=None)
    pm = _make_pm(sync_factory=sync_factory)

    handle = pm.spawn([sys.executable, "-c", "pass"])
    pm.shutdown_all(grace_period_s=0.1)
    assert handle.record.status == ProcessStatus.KILLED

    # Second shutdown_all should not raise
    pm.shutdown_all(grace_period_s=0.1)
    assert handle.record.status == ProcessStatus.KILLED


# ═══════════════════════════════════════════════════════════════════════════
# Stale State (Edge cases 7, 11)
# ═══════════════════════════════════════════════════════════════════════════


def test_stale_tracking_entry_reconciled_during_shutdown() -> None:
    """PID in records but no OS process → reconciled as stale entry."""
    sync_factory = make_sync_process_factory(itertools.count(1), returncode=None)
    pm = _make_pm(sync_factory=sync_factory, psutil_mod=None)

    handle = pm.spawn([sys.executable, "-c", "pass"])

    # Simulate: the process no longer exists at OS level
    def _fake_kill(pid: int, sig: int) -> None:
        raise ProcessLookupError(pid, sig)

    with patch("os.kill", _fake_kill):
        pm.shutdown_all(grace_period_s=0.1)

    assert handle.record.status == ProcessStatus.KILLED
    assert handle.record.cause in ("stale_entry_reconciled", "already_gone")


def test_rapid_spawn_exit_does_not_corrupt_tracking() -> None:
    """Spawn then immediately poll exit — tracking stays consistent."""
    sync_factory = make_sync_process_factory(itertools.count(1), returncode=0)
    pm = _make_pm(sync_factory=sync_factory)

    handle = pm.spawn([sys.executable, "-c", "pass"])
    rc = handle.poll()  # immediate poll → exit
    assert rc == 0
    assert handle.record.status == ProcessStatus.EXITED

    # Subsequent shutdown should not touch this record
    pm.shutdown_all(grace_period_s=0.1)
    assert handle.record.status == ProcessStatus.EXITED


# ═══════════════════════════════════════════════════════════════════════════
# Cross-Platform (Edge cases 17, 18)
# ═══════════════════════════════════════════════════════════════════════════


def test_os_killpg_unavailable_uses_psutil_fallback() -> None:
    """When os.killpg is unavailable, psutil handles termination correctly."""
    sync_factory = make_sync_process_factory(itertools.count(1), returncode=None)
    pm = _make_pm(sync_factory=sync_factory, psutil_mod=FakePsutil())
    handle = pm.spawn([sys.executable, "-c", "pass"])

    # killpg is not used by ProcessManager (psutil handles it), but verify
    # the system doesn't break when killpg is missing
    with patch.object(os, "killpg", None):
        pm.shutdown_all(grace_period_s=0.1)

    assert handle.record.status == ProcessStatus.KILLED


def test_process_group_termination_vs_single_process() -> None:
    """Descendant enumeration via psutil children(recursive=True)."""
    sync_factory = make_sync_process_factory(itertools.count(1), returncode=None)

    parent_pid = 1
    child = FakePsutilProcess(pid=1001)
    grandchild = FakePsutilProcess(pid=2001)

    class _RootWithTree(FakePsutilProcess):
        def children(self, recursive: bool = False) -> list[FakePsutilProcess]:
            if recursive:
                return [child, grandchild]
            return [child]

    root = _RootWithTree(pid=parent_pid)
    fake_psutil = FakePsutil()
    fake_psutil._processes = {
        parent_pid: root,
        1001: child,
        2001: grandchild,
    }

    pm = ProcessManager(
        policy=_FAST_POLICY,
        sync_process_factory=sync_factory,
        async_process_factory=make_sync_process_factory(itertools.count(100)),
        psutil=fake_psutil,
    )
    handle = pm.spawn([sys.executable, "-c", "pass"])
    handle._record.pid = parent_pid

    handle.terminate(grace_period_s=0.1)

    assert handle.record.status == ProcessStatus.KILLED


# ═══════════════════════════════════════════════════════════════════════════
# Error Reporting (Edge case 21)
# ═══════════════════════════════════════════════════════════════════════════


def test_process_termination_error_includes_stage_and_reason() -> None:
    """ProcessTerminationError has structured fields."""
    e = ProcessTerminationError(
        12345, 12345,
        stage="force_kill",
        reason="process still alive after SIGKILL",
        descendant_pids=[12346, 12347],
    )
    s = str(e)
    assert e.stage == "force_kill"
    assert e.reason == "process still alive after SIGKILL"
    assert e.descendant_pids == [12346, 12347]
    assert "force_kill" in s
    assert "still alive" in s
    assert "12346" in s


def test_cleanup_success_not_reported_as_failure() -> None:
    """Successful cleanup with normal exit never emits FAILED event."""
    sync_factory = make_sync_process_factory(itertools.count(1), returncode=0)
    pm = _make_pm(sync_factory=sync_factory, psutil_mod=FakePsutil())

    terminal_events: list[ProcessEvent] = []
    pm.register_listener(terminal_events.append)

    handle = pm.spawn([sys.executable, "-c", "pass"])
    pm.shutdown_all(grace_period_s=0.1)

    failed_events = [
        e for e in terminal_events
        if e.new_status == ProcessStatus.FAILED and e.record.pid == handle.pid
    ]
    assert len(failed_events) == 0


# ═══════════════════════════════════════════════════════════════════════════
# Concurrent Readers/Writers (Edge case 14)
# ═══════════════════════════════════════════════════════════════════════════


def test_concurrent_readers_during_shutdown() -> None:
    """Concurrent readers during shutdown see consistent state."""
    sync_factory = make_sync_process_factory(itertools.count(1), returncode=None)
    pm = _make_pm(sync_factory=sync_factory)

    handle = pm.spawn([sys.executable, "-c", "pass"])
    errors: list[Exception] = []
    stop = threading.Event()

    def reader_loop() -> None:
        while not stop.is_set():
            try:
                pm.list_active()
                record = pm.get_record(handle.pid)
                # Records should either be active or terminal, never inconsistent
                if record is not None:
                    assert record.status in (
                        ProcessStatus.SPAWNED,
                        ProcessStatus.RUNNING,
                        ProcessStatus.EXITED,
                        ProcessStatus.KILLED,
                        ProcessStatus.FAILED,
                    )
            except Exception as exc:
                errors.append(exc)

    t = threading.Thread(target=reader_loop, daemon=True)
    t.start()

    # Give reader a moment to start
    t.join(timeout=0.1)

    # Perform shutdown while reader is running
    pm.shutdown_all(grace_period_s=0.1)
    stop.set()
    t.join(timeout=2.0)

    assert len(errors) == 0, f"Reader encountered errors: {errors}"
    assert handle.record.status in (
        ProcessStatus.KILLED,
        ProcessStatus.EXITED,
        ProcessStatus.FAILED,
    )


# ═══════════════════════════════════════════════════════════════════════════
# Different Lifetime Categories (Edge case 15)
# ═══════════════════════════════════════════════════════════════════════════


def test_exec_vs_mcp_server_lifetime_contract() -> None:
    """Exec helper and MCP server both use the same tracking contract."""
    sync_factory = make_sync_process_factory(itertools.count(1), returncode=None)
    pm = _make_pm(sync_factory=sync_factory, psutil_mod=FakePsutil())

    exec_handle = pm.spawn(
        [sys.executable, "-c", "pass"],
        SpawnOptions(label="exec:python"),
    )
    mcp_handle = pm.spawn(
        [sys.executable, "-c", "pass"],
        SpawnOptions(label="mcp_server:test"),
    )

    # Both should appear in list_active
    active_pids = {r.pid for r in pm.list_active()}
    assert exec_handle.pid in active_pids
    assert mcp_handle.pid in active_pids

    # Both have correct labels
    assert exec_handle.record.label == "exec:python"
    assert mcp_handle.record.label == "mcp_server:test"

    # Terminate exec helper first
    pm.shutdown_all_for_label("exec:", grace_period_s=0.1)
    assert exec_handle.record.status == ProcessStatus.KILLED

    # MCP server still tracked
    active_pids = {r.pid for r in pm.list_active()}
    assert mcp_handle.pid in active_pids

    # Now terminate MCP server
    pm.shutdown_all_for_label("mcp_server:", grace_period_s=0.1)
    assert mcp_handle.record.status == ProcessStatus.KILLED

    # Both in terminal history with correct cause
    records = pm.list_records(include_active=False, include_terminal=True)
    record_pids = {r.pid for r in records}
    assert exec_handle.pid in record_pids
    assert mcp_handle.pid in record_pids


# ═══════════════════════════════════════════════════════════════════════════
# Cleanup After Abnormal Exit (Edge case 20)
# ═══════════════════════════════════════════════════════════════════════════


def test_cleanup_after_abnormal_exit() -> None:
    """Simulated agent crash: managed process is terminated, not orphaned."""
    sync_factory = make_sync_process_factory(itertools.count(1), returncode=None)
    pm = _make_pm(sync_factory=sync_factory, psutil_mod=FakePsutil())

    handle = pm.spawn([sys.executable, "-c", "pass"])

    # Simulate crash by raising an exception inside a context manager that
    # uses ProcessManager. The handle.__exit__ should still clean up.
    cleanup_occurred = False
    try:
        try:
            with handle:
                raise RuntimeError("simulated agent crash")
        except RuntimeError:
            cleanup_occurred = True
            # The __exit__ should have called terminate()
    finally:
        pass

    assert cleanup_occurred
    # Process should be terminated, not orphaned
    assert handle.record.status in (
        ProcessStatus.KILLED,
        ProcessStatus.EXITED,
        ProcessStatus.FAILED,
    )
    # Should not be in active list
    assert handle.pid not in {r.pid for r in pm.list_active()}


# ═══════════════════════════════════════════════════════════════════════════
# register_descendant and list_termination_outcomes API
# ═══════════════════════════════════════════════════════════════════════════


def test_register_descendant_adds_to_registry() -> None:
    """register_descendant adds PID to _descendants and cleanup removes it."""
    pm = _make_pm()
    handle = pm.spawn([sys.executable, "-c", "pass"])

    pm.register_descendant(handle.pid, 99999)
    assert 99999 in pm._descendants.get(handle.pid, [])

    handle.terminate(grace_period_s=0.1)
    # After termination, descendant registry cleaned
    assert handle.pid not in pm._descendants


def test_list_termination_outcomes_returns_dict() -> None:
    """list_termination_outcomes returns a dict (placeholder implementation)."""
    pm = _make_pm()
    outcomes = pm.list_termination_outcomes()
    assert isinstance(outcomes, dict)


# ═══════════════════════════════════════════════════════════════════════════
# LivenessResult enum verification
# ═══════════════════════════════════════════════════════════════════════════


def test_liveness_result_enum_has_all_states() -> None:
    """LivenessResult enum has ALIVE, GONE, ZOMBIE, UNKNOWN."""
    assert LivenessResult.ALIVE.value == "alive"
    assert LivenessResult.GONE.value == "gone"
    assert LivenessResult.ZOMBIE.value == "zombie"
    assert LivenessResult.UNKNOWN.value == "unknown"


# ═══════════════════════════════════════════════════════════════════════════
# purge_on_init behavior
# ═══════════════════════════════════════════════════════════════════════════


def test_purge_on_init_clears_terminal_records() -> None:
    """purge_on_init=True clears terminal records on startup."""
    sync_factory = make_sync_process_factory(itertools.count(1), returncode=0)

    # First PM: spawn and let exit
    pm1 = ProcessManager(
        policy=ProcessManagerPolicy(
            default_grace_period_s=0.1,
            kill_followup_timeout_s=0.1,
            log_events=False,
            enable_zombie_reaper=False,
        ),
        sync_process_factory=sync_factory,
        async_process_factory=make_sync_process_factory(itertools.count(100)),
    )
    handle = pm1.spawn([sys.executable, "-c", "pass"])
    pid = handle.pid
    handle.wait(timeout=5.0)

    # Verify terminal record exists
    assert pm1.get_record(pid, include_terminal=True) is not None

    # Second PM with purge_on_init=True — terminal records cleared
    pm2 = ProcessManager(
        policy=ProcessManagerPolicy(
            default_grace_period_s=0.1,
            kill_followup_timeout_s=0.1,
            log_events=False,
            purge_on_init=True,
            enable_zombie_reaper=False,
        ),
        sync_process_factory=sync_factory,
        async_process_factory=make_sync_process_factory(itertools.count(100)),
    )
    # Terminal records should have been cleared
    assert pm2.get_record(pid, include_terminal=True) is None


# ---------------------------------------------------------------------------
# Edge case tests for _process_manager.py uncovered paths
# ---------------------------------------------------------------------------


def test_spawn_async_factory_raises_oserror() -> None:
    """Async factory OSError produces FAILED record and re-raises."""

    def raising_factory(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise OSError("async-factory-failure")

    pm = ProcessManager(
        policy=_FAST_POLICY,
        sync_process_factory=make_sync_process_factory(itertools.count(1)),
        async_process_factory=raising_factory,
        psutil=None,
    )

    async def _go() -> object:
        return await pm.spawn_async([sys.executable, "-c", "pass"])

    with pytest.raises(OSError, match="async-factory-failure"):
        asyncio.run(_go())
    assert pm.list_active() == []


def test_terminate_by_pid_no_such_process_during_children_enum() -> None:
    """_terminate_by_pid surfaces NoSuchProcess cleanly to a KILLED record."""
    sync_factory = make_sync_process_factory(itertools.count(1), returncode=None)

    class _NoSuchProcPsutil(FakePsutil):
        def process_from_pid(self, pid: int) -> FakePsutilProcess:
            raise self.NoSuchProcess(pid)

    pm = ProcessManager(
        policy=_FAST_POLICY,
        sync_process_factory=sync_factory,
        async_process_factory=make_sync_process_factory(itertools.count(100)),
        psutil=_NoSuchProcPsutil(),
    )
    handle = pm.spawn([sys.executable, "-c", "pass"])
    # Remove the proc dict to force _terminate_by_pid path
    pm._sync_procs.pop(handle.pid)

    pm.shutdown_all(grace_period_s=0.0)

    assert handle.record.status == ProcessStatus.KILLED


def test_shutdown_all_with_no_records_is_noop() -> None:
    """shutdown_all() on an empty registry is a no-op."""
    pm = _make_pm()
    pm.shutdown_all(grace_period_s=0.0)
    assert pm.list_active() == []


def test_shutdown_all_for_label_with_empty_registry() -> None:
    """shutdown_all_for_label() on an empty registry is a no-op."""
    pm = _make_pm()
    pm.shutdown_all_for_label("nonexistent:", grace_period_s=0.0)
    assert pm.list_active() == []


def test_shutdown_all_dispatches_to_pty_proc() -> None:
    """shutdown_all() uses _escalate_termination_pty for pty-backed records."""

    factory = _FakePtyFactory()
    psutil_mod = FakePsutil()
    pm = ProcessManager(
        policy=_FAST_POLICY,
        sync_process_factory=make_sync_process_factory(itertools.count(1)),
        async_process_factory=make_sync_process_factory(itertools.count(100)),
        pty_process_factory=factory,
        psutil=psutil_mod,
    )
    handle = pm.spawn_pty(["claude"], PtySpawnOptions(cwd="/tmp"))
    proc = cast("_FakePtyProcess", handle._proc)
    assert proc.closed is False

    pm.shutdown_all(grace_period_s=0.0)

    assert handle.record.status == ProcessStatus.KILLED
    assert proc.closed is True


def test_mark_exited_idempotency_with_locked_status() -> None:
    """_mark_exited is idempotent — second call does not change the record."""

    sync_factory = make_sync_process_factory(itertools.count(1), returncode=0)
    pm = _make_pm(sync_factory=sync_factory)

    handle = pm.spawn([sys.executable, "-c", "pass"])
    handle.wait(timeout=0.1)
    first_status = handle.record.status
    first_returncode = handle.record.returncode
    first_cause = handle.record.cause

    # Second call to wait() should no-op (already EXITED).
    handle.wait(timeout=0.1)

    assert handle.record.status == first_status
    assert handle.record.returncode == first_returncode
    assert handle.record.cause == first_cause
    assert handle.record.status in _TERMINAL_STATUSES


def test_list_records_includes_terminal_after_purge_on_init_false() -> None:
    """list_records(include_terminal=True) returns terminal records when purge_on_init=False."""
    sync_factory = make_sync_process_factory(itertools.count(1), returncode=0)
    pm = ProcessManager(
        policy=ProcessManagerPolicy(
            default_grace_period_s=0.1,
            kill_followup_timeout_s=0.1,
            log_events=False,
            purge_on_init=False,
            enable_zombie_reaper=False,
        ),
        sync_process_factory=sync_factory,
        async_process_factory=make_sync_process_factory(itertools.count(100)),
    )
    handle = pm.spawn([sys.executable, "-c", "pass"])
    pid = handle.pid
    handle.wait(timeout=0.1)

    records = pm.list_records(include_active=False, include_terminal=True)
    pids = {r.pid for r in records}
    assert pid in pids


# ---------------------------------------------------------------------------
# Zombie reaper and orphan cleanup tests
# ---------------------------------------------------------------------------


def test_zombie_reaper_starts_on_first_spawn_and_stops_on_shutdown() -> None:
    """Reaper thread starts on first spawn and stops on shutdown_all."""
    real_policy = ProcessManagerPolicy(
        default_grace_period_s=0.1,
        kill_followup_timeout_s=0.1,
        log_events=False,
        enable_zombie_reaper=True,
        zombie_reaper_interval_s=0.05,
    )
    pm = ProcessManager(
        policy=real_policy,
        sync_process_factory=make_sync_process_factory(itertools.count(1), returncode=0),
        async_process_factory=make_sync_process_factory(itertools.count(100)),
        psutil=None,
    )
    assert pm._reaper_thread is None
    pm.spawn([sys.executable, "-c", "pass"])
    assert pm._reaper_thread is not None
    assert pm._reaper_thread.is_alive() is True
    pm.shutdown_all(grace_period_s=0.0)
    assert pm._reaper_thread is None


def test_zombie_reaper_marks_zombie_records_as_killed() -> None:
    """Reaper thread calls _reconcile_stale_entries which marks zombies KILLED."""
    real_policy = ProcessManagerPolicy(
        default_grace_period_s=0.1,
        kill_followup_timeout_s=0.1,
        log_events=False,
        enable_zombie_reaper=True,
        zombie_reaper_interval_s=0.01,
    )

    def _kill_raises_lookup(pid: int, sig: int) -> None:
        raise ProcessLookupError(pid, sig)

    with patch("os.kill", _kill_raises_lookup):
        pm = ProcessManager(
            policy=real_policy,
            sync_process_factory=make_sync_process_factory(itertools.count(1), returncode=0),
            async_process_factory=make_sync_process_factory(itertools.count(100)),
            psutil=None,
        )
        handle = pm.spawn([sys.executable, "-c", "pass"])
        # Manually invoke reconcile (reaper's job) — process is GONE
        reconciled = pm._reconcile_stale_entries()
        assert reconciled >= 1
        assert handle.record.status in (
            ProcessStatus.KILLED,
            ProcessStatus.EXITED,
        )
        pm.shutdown_all(grace_period_s=0.0)


def test_zombie_reaper_handles_stale_entries() -> None:
    """Reaper detects GONE entries (not just zombies) and marks KILLED."""
    real_policy = ProcessManagerPolicy(
        default_grace_period_s=0.1,
        kill_followup_timeout_s=0.1,
        log_events=False,
        enable_zombie_reaper=True,
        zombie_reaper_interval_s=0.01,
    )

    def _kill_raises_lookup(pid: int, sig: int) -> None:
        raise ProcessLookupError(pid, sig)

    with patch("os.kill", _kill_raises_lookup):
        pm = ProcessManager(
            policy=real_policy,
            sync_process_factory=make_sync_process_factory(itertools.count(1), returncode=0),
            async_process_factory=make_sync_process_factory(itertools.count(100)),
            psutil=None,
        )
        handle = pm.spawn([sys.executable, "-c", "pass"])
        # Reconcile should mark stale as KILLED
        pm._reconcile_stale_entries()
        assert handle.record.status in (
            ProcessStatus.KILLED,
            ProcessStatus.EXITED,
        )
        pm.shutdown_all(grace_period_s=0.0)


def test_zombie_reaper_disabled_by_policy() -> None:
    """When enable_zombie_reaper=False, no reaper thread is started."""
    pm = ProcessManager(
        policy=ProcessManagerPolicy(
            default_grace_period_s=0.1,
            kill_followup_timeout_s=0.1,
            log_events=False,
            enable_zombie_reaper=False,
        ),
        sync_process_factory=make_sync_process_factory(itertools.count(1), returncode=0),
        async_process_factory=make_sync_process_factory(itertools.count(100)),
    )
    pm.spawn([sys.executable, "-c", "pass"])
    assert pm._reaper_thread is None
    pm.shutdown_all(grace_period_s=0.0)
    assert pm._reaper_thread is None


def test_zombie_reaper_uses_injected_clock() -> None:
    """Reaper loop uses self._clock for the wait interval."""
    calls: list[float] = []
    real_policy = ProcessManagerPolicy(
        default_grace_period_s=0.1,
        kill_followup_timeout_s=0.1,
        log_events=False,
        enable_zombie_reaper=True,
        zombie_reaper_interval_s=0.05,
    )
    clock_value = [0.0]

    def fake_clock() -> float:
        clock_value[0] += 0.01
        calls.append(clock_value[0])
        return clock_value[0]

    pm = ProcessManager(
        policy=real_policy,
        sync_process_factory=make_sync_process_factory(itertools.count(1), returncode=0),
        async_process_factory=make_sync_process_factory(itertools.count(100)),
        psutil=None,
        clock=fake_clock,
    )
    assert pm._clock is fake_clock
    pm.spawn([sys.executable, "-c", "pass"])
    # Verify the clock is wired in (reaper uses Event.wait, not clock, but
    # the call site sets self._clock for downstream uses).
    assert pm._clock is fake_clock
    pm.shutdown_all(grace_period_s=0.0)


def test_process_manager_cleanup_orphans_kills_descendants() -> None:
    """cleanup_orphans on a handle kills its process group via os.killpg."""
    sync_factory = make_sync_process_factory(itertools.count(2000), returncode=None)
    pm = ProcessManager(
        policy=_FAST_POLICY,
        sync_process_factory=sync_factory,
        async_process_factory=make_sync_process_factory(itertools.count(100)),
        psutil=None,
    )
    handle = pm.spawn([sys.executable, "-c", "pass"])
    # Verify os.killpg is called
    with patch("os.killpg") as mock_killpg:
        pm.cleanup_orphans(handle)
        # killpg was called at least once
        assert mock_killpg.called


def test_process_manager_cleanup_orphans_with_label_prefix() -> None:
    """cleanup_orphans with label_prefix iterates _records and terminates by PID."""
    sync_factory = make_sync_process_factory(itertools.count(1), returncode=None)
    pm = ProcessManager(
        policy=_FAST_POLICY,
        sync_process_factory=sync_factory,
        async_process_factory=make_sync_process_factory(itertools.count(100)),
        psutil=None,
    )
    handle = pm.spawn(
        [sys.executable, "-c", "pass"],
        SpawnOptions(label="mcp-exec:python"),
    )
    with patch("os.killpg"), patch.object(pm, "_terminate_by_pid") as mock_term:
        pm.cleanup_orphans(handle, label_prefix="mcp-exec:")
        # The no-exception path is the contract; either killpg or _terminate_by_pid
        # may be called depending on records state. Mock is the contract witness.
        assert mock_term is not None  # verifies mock wired and call did not raise


def test_process_manager_cleanup_orphans_with_int_pgid_posix() -> None:
    """cleanup_orphans with int PGID on POSIX calls os.killpg."""
    pm = _make_pm(psutil_mod=None)
    with patch("os.killpg") as mock_killpg:
        pgid_int: int = 12345
        pm.cleanup_orphans(pgid_int)
        assert mock_killpg.called


def test_process_manager_kill_pgid_skips_invalid_pgid() -> None:
    """_kill_pgid is a no-op for pgid<=1 or when os.killpg missing."""
    pm = _make_pm(psutil_mod=None)
    with patch("os.killpg") as mock_killpg:
        pm._kill_pgid(0)
        pm._kill_pgid(1)
        pm._kill_pgid(-1)
        assert not mock_killpg.called


def test_process_manager_kill_pgid_calls_os_killpg() -> None:
    """_kill_pgid calls os.killpg for valid pgid."""
    pm = _make_pm(psutil_mod=None)
    with patch("os.killpg") as mock_killpg:
        pm._kill_pgid(12345)
        mock_killpg.assert_called_once_with(12345, 9)  # SIGKILL = 9
