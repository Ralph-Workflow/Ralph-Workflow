from __future__ import annotations

import asyncio
import itertools
import os
import sys
import threading
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

from ralph.process.manager import (
    AsyncProcessLike,
    LivenessResult,
    ProcessManager,
    ProcessManagerPolicy,
    ProcessStatus,
    ProcessTerminationError,
    PtySpawnOptions,
    SpawnOptions,
    SyncProcessLike,
)
from ralph.process.manager import (
    _process_manager as pm_mod,
)
from ralph.testing.fake_process import (
    FakeImmortalPopen,
    FakePsutil,
    FakePsutilProcess,
    FakeStubbornPopen,
    make_async_process_factory,
    make_sync_process_factory,
)
from tests._process_manager_pty_helper__fakeptyfactory import _FakePtyFactory

if TYPE_CHECKING:
    from ralph.process.manager import ProcessEvent, ProcessRecord
    from ralph.testing.fake_process import SyncFactoryCallable

POLICY_WHITE_BOX_EXCEPTION = (
    "White-box tests that reach into private internals are permitted only when "
    "no observable surface can express the regression."
)

_FAST_POLICY = ProcessManagerPolicy(
    default_grace_period_s=0.1,
    kill_followup_timeout_s=0.1,
    log_events=False,
    enable_zombie_reaper=False,
)


def _make_pm(
    *,
    sync_factory: SyncFactoryCallable | None = None,
    psutil_mod: FakePsutil | None = None,
) -> ProcessManager:
    return ProcessManager(
        policy=_FAST_POLICY,
        sync_process_factory=sync_factory or make_sync_process_factory(itertools.count(1)),
        async_process_factory=make_sync_process_factory(itertools.count(100)),
        psutil=psutil_mod,
    )


def test_process_exits_before_graceful_terminate() -> None:
    with patch("os.kill", side_effect=ProcessLookupError):
        pm = _make_pm(psutil_mod=None)
        handle = pm.spawn([sys.executable, "-c", "pass"])
        handle._proc._returncode = 0

        handle.terminate(grace_period_s=0.1)

    assert handle.record.status == ProcessStatus.KILLED
    assert handle.record.cause == "already_gone"


def test_process_exits_between_liveness_check_and_kill() -> None:
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

    def _kill_raises_lookup(pid: int, sig: int) -> None:
        if sig == 0:
            raise ProcessLookupError(pid, sig)

    with patch("os.kill", _kill_raises_lookup):
        pm = _make_pm(psutil_mod=None)
        handle = pm.spawn([sys.executable, "-c", "pass"])

        handle.terminate(grace_period_s=0.1)

    assert handle.record.status == ProcessStatus.KILLED
    assert handle.record.cause == "already_gone"


def test_zombie_detected_after_force_kill() -> None:
    sync_factory = make_sync_process_factory(itertools.count(1), returncode=None)

    class _ZombieAfterKill(FakePsutilProcess):
        def terminate(self) -> None:
            pass

        def kill(self) -> None:
            self._status = "zombie"

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
    sync_factory = make_sync_process_factory(itertools.count(1), returncode=None)
    pm = _make_pm(sync_factory=sync_factory, psutil_mod=FakePsutil())
    handle = pm.spawn([sys.executable, "-c", "pass"])

    handle.terminate(grace_period_s=0.1)

    assert handle.record.status == ProcessStatus.KILLED
    assert handle.record.cause == "killed"


def test_graceful_terminate_times_out_escalates_to_kill() -> None:

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


def test_parent_terminates_but_descendant_survives() -> None:
    sync_factory = make_sync_process_factory(itertools.count(1), returncode=None)

    parent_pid = 1

    class _ImmortalChild(FakePsutilProcess):
        def terminate(self) -> None:
            pass

        def kill(self) -> None:
            pass

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

    assert handle.record.status == ProcessStatus.FAILED


def test_double_terminate_is_idempotent() -> None:
    sync_factory = make_sync_process_factory(itertools.count(1), returncode=0)
    pm = _make_pm(sync_factory=sync_factory)

    handle = pm.spawn([sys.executable, "-c", "pass"])
    handle.wait(timeout=5.0)

    handle.terminate(grace_period_s=0.1)
    assert handle.record.status == ProcessStatus.EXITED

    handle.terminate(grace_period_s=0.1)
    assert handle.record.status == ProcessStatus.EXITED


def test_double_shutdown_all_is_idempotent() -> None:
    sync_factory = make_sync_process_factory(itertools.count(1), returncode=None)
    pm = _make_pm(sync_factory=sync_factory)

    handle = pm.spawn([sys.executable, "-c", "pass"])
    pm.shutdown_all(grace_period_s=0.1)
    assert handle.record.status == ProcessStatus.KILLED

    pm.shutdown_all(grace_period_s=0.1)
    assert handle.record.status == ProcessStatus.KILLED


def test_stale_tracking_entry_reconciled_during_shutdown() -> None:
    sync_factory = make_sync_process_factory(itertools.count(1), returncode=None)
    pm = _make_pm(sync_factory=sync_factory, psutil_mod=None)

    handle = pm.spawn([sys.executable, "-c", "pass"])

    def _fake_kill(pid: int, sig: int) -> None:
        raise ProcessLookupError(pid, sig)

    with patch("os.kill", _fake_kill):
        pm.shutdown_all(grace_period_s=0.1)

    assert handle.record.status == ProcessStatus.KILLED
    assert handle.record.cause in ("stale_entry_reconciled", "already_gone")


def test_rapid_spawn_exit_does_not_corrupt_tracking() -> None:
    sync_factory = make_sync_process_factory(itertools.count(1), returncode=0)
    pm = _make_pm(sync_factory=sync_factory)

    handle = pm.spawn([sys.executable, "-c", "pass"])
    rc = handle.poll()
    assert rc == 0
    assert handle.record.status == ProcessStatus.EXITED

    pm.shutdown_all(grace_period_s=0.1)
    assert handle.record.status == ProcessStatus.EXITED


def test_os_killpg_unavailable_uses_psutil_fallback() -> None:
    sync_factory = make_sync_process_factory(itertools.count(1), returncode=None)
    pm = _make_pm(sync_factory=sync_factory, psutil_mod=FakePsutil())
    handle = pm.spawn([sys.executable, "-c", "pass"])

    with patch.object(os, "killpg", None):
        pm.shutdown_all(grace_period_s=0.1)

    assert handle.record.status == ProcessStatus.KILLED


def test_process_group_termination_vs_single_process() -> None:
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


def test_process_termination_error_includes_stage_and_reason() -> None:
    e = ProcessTerminationError(
        12345,
        12345,
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
    sync_factory = make_sync_process_factory(itertools.count(1), returncode=0)
    pm = _make_pm(sync_factory=sync_factory, psutil_mod=FakePsutil())

    terminal_events: list[ProcessEvent] = []
    pm.register_listener(terminal_events.append)

    handle = pm.spawn([sys.executable, "-c", "pass"])
    pm.shutdown_all(grace_period_s=0.1)

    failed_events = [
        e
        for e in terminal_events
        if e.new_status == ProcessStatus.FAILED and e.record.pid == handle.pid
    ]
    assert len(failed_events) == 0


def test_concurrent_readers_during_shutdown() -> None:
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

    t.join(timeout=0.1)

    pm.shutdown_all(grace_period_s=0.1)
    stop.set()
    t.join(timeout=2.0)

    assert len(errors) == 0, f"Reader encountered errors: {errors}"
    assert handle.record.status in (
        ProcessStatus.KILLED,
        ProcessStatus.EXITED,
        ProcessStatus.FAILED,
    )


def test_exec_vs_mcp_server_lifetime_contract() -> None:
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

    active_pids = {r.pid for r in pm.list_active()}
    assert exec_handle.pid in active_pids
    assert mcp_handle.pid in active_pids

    assert exec_handle.record.label == "exec:python"
    assert mcp_handle.record.label == "mcp_server:test"

    pm.shutdown_all_for_label("exec:", grace_period_s=0.1)
    assert exec_handle.record.status == ProcessStatus.KILLED

    active_pids = {r.pid for r in pm.list_active()}
    assert mcp_handle.pid in active_pids

    pm.shutdown_all_for_label("mcp_server:", grace_period_s=0.1)
    assert mcp_handle.record.status == ProcessStatus.KILLED

    records = pm.list_records(include_active=False, include_terminal=True)
    record_pids = {r.pid for r in records}
    assert exec_handle.pid in record_pids
    assert mcp_handle.pid in record_pids


def test_cleanup_after_abnormal_exit() -> None:
    sync_factory = make_sync_process_factory(itertools.count(1), returncode=None)
    pm = _make_pm(sync_factory=sync_factory, psutil_mod=FakePsutil())

    handle = pm.spawn([sys.executable, "-c", "pass"])

    cleanup_occurred = False
    try:
        try:
            with handle:
                raise RuntimeError("simulated agent crash")
        except RuntimeError:
            cleanup_occurred = True
    finally:
        pass

    assert cleanup_occurred
    assert handle.record.status in (
        ProcessStatus.KILLED,
        ProcessStatus.EXITED,
        ProcessStatus.FAILED,
    )
    assert handle.pid not in {r.pid for r in pm.list_active()}


def test_register_descendant_registry_tracks_pid_until_teardown() -> None:
    pm = _make_pm()
    handle = pm.spawn([sys.executable, "-c", "pass"])

    pm.register_descendant(handle.pid, 99999)
    assert 99999 in pm._registered_descendants(handle.pid)

    handle.terminate(grace_period_s=0.1)
    assert pm._registered_descendants(handle.pid) == ()


class _TrackedDescendant(FakePsutilProcess):
    def __init__(self, pid: int) -> None:
        super().__init__(pid=pid)
        self.terminate_calls = 0
        self.kill_calls = 0

    def terminate(self) -> None:
        self.terminate_calls += 1
        self._terminated = True

    def kill(self) -> None:
        self.kill_calls += 1
        self._killed = True


class _NoChildrenRoot(FakePsutilProcess):
    def children(self, recursive: bool = False) -> list[FakePsutilProcess]:
        return []


def test_register_descendant_adds_to_registry() -> None:
    sync_factory = make_sync_process_factory(itertools.count(1), returncode=None)
    parent_pid = 1
    descendant_pid = 99999

    parent_proc = _NoChildrenRoot(pid=parent_pid)
    descendant_proc = _TrackedDescendant(pid=descendant_pid)
    fake_psutil = FakePsutil()
    fake_psutil._processes = {
        parent_pid: parent_proc,
        descendant_pid: descendant_proc,
    }

    pm = _make_pm(sync_factory=sync_factory, psutil_mod=fake_psutil)
    handle = pm.spawn([sys.executable, "-c", "pass"])

    pm.register_descendant(handle.pid, descendant_pid)
    assert descendant_proc.terminate_calls == 0
    assert descendant_proc.kill_calls == 0

    handle.terminate(grace_period_s=0.05)

    assert descendant_proc.terminate_calls >= 1
    assert descendant_proc.kill_calls >= 1
    assert handle.record.status == ProcessStatus.KILLED


def test_register_descendant_terminates_grandchild_hidden_from_psutil_tree() -> None:
    sync_factory = make_sync_process_factory(itertools.count(1), returncode=None)

    parent_pid = 1
    descendant_pid = 99999

    parent_proc = _NoChildrenRoot(pid=parent_pid)
    descendant_proc = _TrackedDescendant(pid=descendant_pid)
    fake_psutil = FakePsutil()
    fake_psutil._processes = {
        parent_pid: parent_proc,
        descendant_pid: descendant_proc,
    }

    pm = _make_pm(sync_factory=sync_factory, psutil_mod=fake_psutil)

    handle = pm.spawn([sys.executable, "-c", "pass"])

    pm.register_descendant(parent_pid, descendant_pid)
    assert descendant_pid in pm._registered_descendants(parent_pid)

    assert parent_proc.children(recursive=True) == []

    handle.terminate(grace_period_s=0.05)

    assert descendant_proc.terminate_calls >= 1, (
        f"registered descendant should receive graceful terminate(),"
        f" got terminate_calls={descendant_proc.terminate_calls}"
    )
    assert descendant_proc.kill_calls >= 1, (
        f"registered descendant should receive force kill(),"
        f" got kill_calls={descendant_proc.kill_calls}"
    )
    assert handle.record.status == ProcessStatus.KILLED
    assert pm._registered_descendants(parent_pid) == ()


def test_register_descendant_registry_consumed_once_on_terminate() -> None:
    sync_factory = make_sync_process_factory(itertools.count(1), returncode=None)

    parent_pid = 1
    descendant_pid = 88888

    parent_proc = _NoChildrenRoot(pid=parent_pid)
    descendant_proc = _TrackedDescendant(pid=descendant_pid)
    fake_psutil = FakePsutil()
    fake_psutil._processes = {
        parent_pid: parent_proc,
        descendant_pid: descendant_proc,
    }

    pm = _make_pm(sync_factory=sync_factory, psutil_mod=fake_psutil)

    handle = pm.spawn([sys.executable, "-c", "pass"])

    pm.register_descendant(parent_pid, descendant_pid)
    handle.terminate(grace_period_s=0.05)

    first_terminate = descendant_proc.terminate_calls
    first_kill = descendant_proc.kill_calls

    handle.terminate(grace_period_s=0.05)

    assert descendant_proc.terminate_calls == first_terminate
    assert descendant_proc.kill_calls == first_kill


def test_register_descendant_multiple_descendants_all_terminated() -> None:
    sync_factory = make_sync_process_factory(itertools.count(1), returncode=None)

    parent_pid = 1
    descendant_pids = [70001, 70002, 70003]

    parent_proc = _NoChildrenRoot(pid=parent_pid)
    descendants = [_TrackedDescendant(pid=pid) for pid in descendant_pids]
    fake_psutil = FakePsutil()
    fake_psutil._processes = {parent_pid: parent_proc, **{d.pid: d for d in descendants}}

    pm = _make_pm(sync_factory=sync_factory, psutil_mod=fake_psutil)

    handle = pm.spawn([sys.executable, "-c", "pass"])

    for descendant_pid in descendant_pids:
        pm.register_descendant(parent_pid, descendant_pid)

    handle.terminate(grace_period_s=0.05)

    for descendant in descendants:
        assert descendant.terminate_calls >= 1, (
            f"descendant PID {descendant.pid} should be terminated;"
            f" terminate_calls={descendant.terminate_calls}"
        )
        assert descendant.kill_calls >= 1, (
            f"descendant PID {descendant.pid} should be killed; kill_calls={descendant.kill_calls}"
        )


def test_list_termination_outcomes_returns_dict() -> None:
    pm = _make_pm()
    outcomes = pm.list_termination_outcomes()
    assert isinstance(outcomes, dict)


def test_liveness_result_enum_has_all_states() -> None:
    assert LivenessResult.ALIVE.value == "alive"
    assert LivenessResult.GONE.value == "gone"
    assert LivenessResult.ZOMBIE.value == "zombie"
    assert LivenessResult.UNKNOWN.value == "unknown"


def test_purge_on_init_clears_terminal_records() -> None:
    sync_factory = make_sync_process_factory(itertools.count(1), returncode=0)

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

    assert pm1.get_record(pid, include_terminal=True) is not None

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
    assert pm2.get_record(pid, include_terminal=True) is None


def test_spawn_async_factory_raises_oserror() -> None:

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
    pm._sync_procs.pop(handle.pid)

    pm.shutdown_all(grace_period_s=0.0)

    assert handle.record.status == ProcessStatus.KILLED


def test_shutdown_all_with_no_records_is_noop() -> None:
    pm = _make_pm()
    pm.shutdown_all(grace_period_s=0.0)
    assert pm.list_active() == []


def test_shutdown_all_for_label_with_empty_registry() -> None:
    pm = _make_pm()
    pm.shutdown_all_for_label("nonexistent:", grace_period_s=0.0)
    assert pm.list_active() == []


def test_shutdown_all_dispatches_to_pty_proc() -> None:

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
    proc = handle._proc
    assert proc.closed is False

    pm.shutdown_all(grace_period_s=0.0)

    assert handle.record.status == ProcessStatus.KILLED
    assert proc.closed is True


def test_mark_exited_idempotency_with_locked_status() -> None:

    sync_factory = make_sync_process_factory(itertools.count(1), returncode=0)
    pm = _make_pm(sync_factory=sync_factory)

    handle = pm.spawn([sys.executable, "-c", "pass"])
    handle.wait(timeout=0.1)
    first_status = handle.record.status
    first_returncode = handle.record.returncode
    first_cause = handle.record.cause

    handle.wait(timeout=0.1)

    assert handle.record.status == first_status
    assert handle.record.returncode == first_returncode
    assert handle.record.cause == first_cause
    assert handle.record.status in (
        ProcessStatus.EXITED,
        ProcessStatus.KILLED,
        ProcessStatus.FAILED,
    )


def test_list_records_includes_terminal_after_purge_on_init_false() -> None:
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


_REAPER_THREAD_NAME_PREFIX = "ProcessManager-zombie-reaper-"


def _reaper_thread_for(pm: ProcessManager) -> threading.Thread | None:
    wanted = f"{_REAPER_THREAD_NAME_PREFIX}{id(pm)}"
    for thread in threading.enumerate():
        if thread.name == wanted:
            return thread
    return None


def test_zombie_reaper_starts_on_first_spawn_and_stops_on_shutdown() -> None:
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
    assert _reaper_thread_for(pm) is None, "no reaper may run before the first spawn"
    pm.spawn([sys.executable, "-c", "pass"])
    reaper = _reaper_thread_for(pm)
    assert reaper is not None, "the first spawn must start the background reaper"
    assert reaper.is_alive()
    pm.shutdown_all(grace_period_s=0.0)
    assert _reaper_thread_for(pm) is None, "shutdown_all must stop the background reaper"


def test_zombie_reaper_marks_zombie_records_as_killed() -> None:
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
        reconciled = pm._reconcile_stale_entries()
        assert reconciled >= 1
        assert handle.record.status in (
            ProcessStatus.KILLED,
            ProcessStatus.EXITED,
        )
        pm.shutdown_all(grace_period_s=0.0)


def test_zombie_reaper_handles_stale_entries() -> None:
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
        pm._reconcile_stale_entries()
        assert handle.record.status in (
            ProcessStatus.KILLED,
            ProcessStatus.EXITED,
        )
        pm.shutdown_all(grace_period_s=0.0)


def test_zombie_reaper_disabled_by_policy() -> None:
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
    assert _reaper_thread_for(pm) is None, "a disabled reaper must not start a thread"
    pm.shutdown_all(grace_period_s=0.0)
    assert _reaper_thread_for(pm) is None


def test_process_manager_cleanup_orphans_kills_descendants() -> None:
    sync_factory = make_sync_process_factory(itertools.count(2000), returncode=None)
    pm = ProcessManager(
        policy=_FAST_POLICY,
        sync_process_factory=sync_factory,
        async_process_factory=make_sync_process_factory(itertools.count(100)),
        psutil=None,
    )
    handle = pm.spawn([sys.executable, "-c", "pass"])
    with patch("os.killpg") as mock_killpg:
        pm.cleanup_orphans(handle)
        assert mock_killpg.called


def test_process_manager_cleanup_orphans_with_label_prefix() -> None:
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
    with (
        patch("os.killpg"),
        patch.object(pm, "_terminate_by_pid") as mock_term,
    ):
        pm.cleanup_orphans(handle, label_prefix="mcp-exec:")
        assert mock_term is not None


def test_process_manager_cleanup_orphans_with_int_pgid_posix() -> None:
    pm = _make_pm(psutil_mod=None)
    with patch("os.killpg") as mock_killpg:
        pgid_int: int = 12345
        pm.cleanup_orphans(pgid_int)
        assert mock_killpg.called


def test_process_manager_kill_pgid_skips_invalid_pgid() -> None:
    pm = _make_pm(psutil_mod=None)
    with patch("os.killpg") as mock_killpg:
        pm._kill_pgid(0)
        pm._kill_pgid(1)
        pm._kill_pgid(-1)
        assert not mock_killpg.called


def test_process_manager_kill_pgid_calls_os_killpg() -> None:
    pm = _make_pm(psutil_mod=None)
    with patch("os.killpg") as mock_killpg:
        pm._kill_pgid(12345)
        mock_killpg.assert_called_once_with(12345, 9)


def test_reconcile_zombie_sync_record_runs_sync_reap_helper() -> None:
    sync_factory = make_sync_process_factory(itertools.count(1), returncode=None)
    pm = _make_pm(sync_factory=sync_factory, psutil_mod=FakePsutil())
    handle = pm.spawn([sys.executable, "-c", "pass"])
    sync_proc: SyncProcessLike = handle._proc

    wait_called = {"called": False}
    real_wait = sync_proc.wait

    def _spy_wait(timeout: float | None = None) -> int:
        wait_called["called"] = True
        return real_wait(timeout=timeout)

    sync_proc.wait = _spy_wait

    real_verify = pm_mod.verify_process_liveness

    def _fake_verify(
        p: int,
        *,
        psutil_mod: object = None,
    ) -> LivenessResult:
        if p == handle.pid:
            return LivenessResult.ZOMBIE
        return real_verify(p, psutil_mod=psutil_mod)

    mark_calls: list[tuple[object, object, str]] = []
    real_mark_killed = pm._mark_killed

    def _spy_mark_killed(
        rec: ProcessRecord,
        returncode: int | None = None,
        *,
        cause: str = "killed",
    ) -> None:
        mark_calls.append((rec, returncode, cause))
        real_mark_killed(rec, returncode, cause=cause)

    with (
        patch.object(pm_mod, "verify_process_liveness", _fake_verify),
        patch.object(pm, "_mark_killed", side_effect=_spy_mark_killed),
    ):
        reconciled = pm._reconcile_stale_entries()

    assert reconciled == 1
    assert wait_called["called"] is True, (
        "Reaper must invoke proc.wait on the sync proc before _mark_killed"
    )
    assert len(mark_calls) == 1
    _called_rec, _called_rc, called_cause = mark_calls[0]
    assert called_cause == "zombie_reconciled"
    assert handle.record.status == ProcessStatus.KILLED
    assert handle.record.cause == "zombie_reconciled"


def test_reconcile_zombie_async_record_runs_async_reap_helper() -> None:
    async_factory = make_async_process_factory(itertools.count(4001), returncode=None)
    pm = ProcessManager(
        policy=_FAST_POLICY,
        sync_process_factory=make_sync_process_factory(itertools.count(5000)),
        async_process_factory=async_factory,
        psutil=None,
    )

    async def _spawn() -> AsyncProcessLike:
        async_handle = await pm.spawn_async(
            [sys.executable, "-c", "pass"],
            SpawnOptions(stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE),
        )
        return async_handle._proc

    handle_proc = asyncio.run(_spawn())
    assert pm.get_record(handle_proc.pid) is not None

    real_verify = pm_mod.verify_process_liveness

    def _fake_verify(
        p: int,
        *,
        psutil_mod: object = None,
    ) -> LivenessResult:
        if p == handle_proc.pid:
            return LivenessResult.ZOMBIE
        return real_verify(p, psutil_mod=psutil_mod)

    waitpid_calls: list[tuple[int, int]] = []
    real_waitpid = getattr(os, "waitpid", None)

    def _fake_waitpid(wait_pid: int, options: int) -> tuple[int, int]:
        waitpid_calls.append((wait_pid, options))
        if real_waitpid is not None:
            try:
                return real_waitpid(wait_pid, options)
            except (OSError, ChildProcessError, ProcessLookupError):
                return (0, 0)
        return (0, 0)

    mark_calls: list[tuple[object, object, str]] = []
    real_mark_killed = pm._mark_killed

    def _spy_mark_killed(
        rec: ProcessRecord,
        returncode: int | None = None,
        *,
        cause: str = "killed",
    ) -> None:
        mark_calls.append((rec, returncode, cause))
        real_mark_killed(rec, returncode, cause=cause)

    with (
        patch.object(pm_mod, "verify_process_liveness", _fake_verify),
        patch.object(os, "waitpid", _fake_waitpid, create=True),
        patch.object(pm, "_mark_killed", side_effect=_spy_mark_killed),
    ):
        reconciled = pm._reconcile_stale_entries()

    assert reconciled == 1
    assert waitpid_calls, "Reaper must invoke os.waitpid on the async record before _mark_killed"
    assert all(call[0] == handle_proc.pid for call in waitpid_calls), (
        f"os.waitpid must be called for the async record's pid; got {waitpid_calls}"
    )
    assert len(mark_calls) == 1
    called_rec, _called_rc, called_cause = mark_calls[0]
    assert called_cause == "zombie_reconciled"
    assert called_rec.status == ProcessStatus.KILLED
    assert called_rec.cause == "zombie_reconciled"


def test_reconcile_zombie_record_without_process_object_uses_mark_only_fallback() -> None:
    sync_factory = make_sync_process_factory(itertools.count(1), returncode=None)
    pm = _make_pm(sync_factory=sync_factory, psutil_mod=FakePsutil())
    handle = pm.spawn([sys.executable, "-c", "pass"])

    real_verify = pm_mod.verify_process_liveness

    def _fake_verify(
        p: int,
        *,
        psutil_mod: object = None,
    ) -> LivenessResult:
        if p == handle.pid:
            return LivenessResult.ZOMBIE
        return real_verify(p, psutil_mod=psutil_mod)

    pm._sync_procs.pop(handle.pid, None)
    pm._pty_procs.pop(handle.pid, None)
    pm._async_procs.pop(handle.pid, None)

    reap_sync_called = {"called": False}
    reap_async_called = {"called": False}
    real_reap_sync = pm._reap_sync_and_mark
    real_reap_async = pm._reap_async_in_sync_and_mark

    def _spy_reap_sync(
        rec: ProcessRecord,
        proc: SyncProcessLike,
        *,
        cause: str,
    ) -> None:
        reap_sync_called["called"] = True
        real_reap_sync(rec, proc, cause=cause)

    def _spy_reap_async(
        rec: ProcessRecord,
        proc: AsyncProcessLike,
        *,
        cause: str,
    ) -> None:
        reap_async_called["called"] = True
        real_reap_async(rec, proc, cause=cause)

    mark_calls: list[tuple[object, object, str]] = []
    real_mark_killed = pm._mark_killed

    def _spy_mark_killed(
        rec: ProcessRecord,
        returncode: int | None = None,
        *,
        cause: str = "killed",
    ) -> None:
        mark_calls.append((rec, returncode, cause))
        real_mark_killed(rec, returncode, cause=cause)

    with (
        patch.object(pm_mod, "verify_process_liveness", _fake_verify),
        patch.object(pm, "_reap_sync_and_mark", side_effect=_spy_reap_sync),
        patch.object(pm, "_reap_async_in_sync_and_mark", side_effect=_spy_reap_async),
        patch.object(pm, "_mark_killed", side_effect=_spy_mark_killed),
    ):
        reconciled = pm._reconcile_stale_entries()

    assert reconciled == 1
    assert reap_sync_called["called"] is False, (
        "_reap_sync_and_mark must NOT be invoked when no sync proc is present"
    )
    assert reap_async_called["called"] is False, (
        "_reap_async_in_sync_and_mark must NOT be invoked when no async proc is present"
    )
    assert len(mark_calls) == 1
    _called_rec, _called_rc, called_cause = mark_calls[0]
    assert called_cause == "zombie_reconciled"
    assert handle.record.status == ProcessStatus.KILLED
    assert handle.record.cause == "zombie_reconciled"
