from __future__ import annotations

import asyncio
import atexit
import itertools
import os
import sys
import threading
import time as _time
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest
from loguru import logger

from ralph.process import (
    ProcessEvent,
    ProcessManager,
    ProcessManagerPolicy,
    ProcessStatus,
    SpawnOptions,
    get_process_manager,
    install_process_manager,
    process_phase_scope,
    reset_process_manager,
    shutdown_process_manager,
)
from ralph.process.manager import (
    LivenessResult,
    ProcessRecord,
    ProcessTerminationError,
    ProcessTerminationIssuer,
    loguru_event_listener,
)
from ralph.testing.fake_process import (
    FakeAsyncProcess,
    FakeImmortalPopen,
    FakePopen,
    FakePsutil,
    FakePsutilProcess,
    FakeStubbornPopen,
    ProcessState,
    make_async_process_factory,
    make_sync_process_factory,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from ralph.testing.fake_process import AsyncFactoryCallable, SyncFactoryCallable


class _RecordingAsyncProc(FakeAsyncProcess):
    def __init__(self, pid: int) -> None:
        super().__init__(pid=pid, state=ProcessState(returncode=None))
        self.terminate_calls = 0
        self.kill_calls = 0

    def terminate(self, **_kwargs: object) -> None:
        self.terminate_calls += 1
        super().terminate()

    def kill(self, **_kwargs: object) -> None:
        self.kill_calls += 1
        super().kill()

    @property
    def termination_calls(self) -> int:
        return self.terminate_calls + self.kill_calls


def _recording_async_factory(created: list[_RecordingAsyncProc]) -> AsyncFactoryCallable:

    async def factory(
        command: Sequence[str],
        *,
        cwd: str | None,
        env: dict[str, str] | None,
        stdin: int | None,
        stdout: int | None,
        stderr: int | None,
        start_new_session: bool,
    ) -> _RecordingAsyncProc:
        del command, cwd, env, stdin, stdout, stderr, start_new_session
        proc = _RecordingAsyncProc(pid=9001 + len(created))
        created.append(proc)
        return proc

    return factory


_FAST_POLICY = ProcessManagerPolicy(
    default_grace_period_s=0.3,
    kill_followup_timeout_s=0.5,
    log_events=False,
    enable_zombie_reaper=False,
)


@pytest.fixture(autouse=True)
def _reset_pm() -> object:
    reset_process_manager()
    yield
    reset_process_manager()


def _make_pm(
    *,
    sync_factory: SyncFactoryCallable | None = None,
    async_factory: AsyncFactoryCallable | None = None,
    psutil_mod: FakePsutil | None = None,
) -> ProcessManager:
    return ProcessManager(
        policy=_FAST_POLICY,
        sync_process_factory=sync_factory or make_sync_process_factory(itertools.count(1)),
        async_process_factory=async_factory or make_async_process_factory(itertools.count(1)),
        psutil=psutil_mod,
    )


def test_spawn_tracks_lifecycle_to_exited() -> None:
    sync_factory = make_sync_process_factory(itertools.count(1), returncode=0)
    pm = _make_pm(sync_factory=sync_factory)
    events: list[ProcessEvent] = []
    pm.register_listener(events.append)

    handle = pm.spawn([sys.executable, "-c", "pass"])
    assert handle.record.status == ProcessStatus.RUNNING
    assert handle.record.pid >= 1

    handle.wait(timeout=5.0)

    assert handle.record.status == ProcessStatus.EXITED
    assert handle.record.cause == "exited"
    assert handle.record.returncode == 0
    statuses = [e.new_status for e in events]
    assert ProcessStatus.RUNNING in statuses
    assert ProcessStatus.EXITED in statuses


def test_terminate_escalates_to_sigkill() -> None:
    never_die_factory = make_sync_process_factory(itertools.count(1), returncode=-9)
    pm = _make_pm(sync_factory=never_die_factory)

    handle = pm.spawn([sys.executable, "-c", "pass"])
    handle.terminate(grace_period_s=0.1, issuer=ProcessTerminationIssuer.OPERATOR)

    assert handle.record.status == ProcessStatus.KILLED
    assert handle.record.cause == "killed"


def test_list_active_returns_labeled_running_records() -> None:
    pm = _make_pm()
    h1 = pm.spawn(
        [sys.executable, "-c", "pass"], SpawnOptions(label="phase:development:mcp-server")
    )
    h2 = pm.spawn([sys.executable, "-c", "pass"], SpawnOptions(label="invoke:dev-agent"))
    active = pm.list_active()
    labels = {r.label for r in active}
    assert "phase:development:mcp-server" in labels
    assert "invoke:dev-agent" in labels
    h1.terminate(grace_period_s=0.1, issuer=ProcessTerminationIssuer.OPERATOR)
    h2.terminate(grace_period_s=0.1, issuer=ProcessTerminationIssuer.OPERATOR)


def test_list_active_excludes_terminated_processes() -> None:
    sync_factory = make_sync_process_factory(itertools.count(1), returncode=0)
    pm = _make_pm(sync_factory=sync_factory)
    handle = pm.spawn([sys.executable, "-c", "pass"], SpawnOptions(label="phase:test"))
    handle.wait(timeout=2.0)
    active = pm.list_active()
    assert all(r.label != "phase:test" for r in active)


def test_list_active_unlabeled_processes_not_included_when_filtering_labels() -> None:
    pm = _make_pm()
    handle = pm.spawn([sys.executable, "-c", "pass"])
    active = pm.list_active()
    unlabeled = [r for r in active if r.label is None]
    assert any(r.pid == handle.pid for r in unlabeled)
    handle.terminate(grace_period_s=0.1, issuer=ProcessTerminationIssuer.OPERATOR)


@pytest.mark.asyncio
async def test_spawn_async_captures_output() -> None:
    async_factory = make_async_process_factory(itertools.count(1), returncode=0)
    pm = _make_pm(async_factory=async_factory)
    events: list[ProcessEvent] = []
    pm.register_listener(events.append)

    handle = await pm.spawn_async(
        [sys.executable, "-c", "print('hello async')"],
        SpawnOptions(stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE),
    )
    stdout, _stderr = await handle.communicate()

    assert stdout == b""
    assert handle.record.status == ProcessStatus.EXITED
    assert handle.record.cause == "exited"


def test_shutdown_all_kills_multiple_children() -> None:
    sync_factory = make_sync_process_factory(itertools.count(1), returncode=None)
    pm = _make_pm(sync_factory=sync_factory)

    handles = [pm.spawn([sys.executable, "-c", "pass"]) for _ in range(3)]
    pids = [h.record.pid for h in handles]
    assert all(h.record.status == ProcessStatus.RUNNING for h in handles)

    pm.shutdown_all(grace_period_s=0)

    for h in handles:
        assert h.record.status in (ProcessStatus.KILLED, ProcessStatus.EXITED)
    active_pids = [r.pid for r in pm.list_active()]
    for pid in pids:
        assert pid not in active_pids


def test_listener_receives_one_event_per_transition_and_unsubscribe_works() -> None:
    sync_factory = make_sync_process_factory(itertools.count(1), returncode=0)
    pm = _make_pm(sync_factory=sync_factory)
    events_a: list[ProcessEvent] = []
    events_b: list[ProcessEvent] = []

    unsub_a = pm.register_listener(events_a.append)
    _unsub_b = pm.register_listener(events_b.append)

    handle = pm.spawn([sys.executable, "-c", "pass"])
    handle.wait(timeout=5.0)

    unsub_a()

    handle2 = pm.spawn([sys.executable, "-c", "pass"])
    handle2.wait(timeout=5.0)

    expected_events_a = 2
    expected_events_b = 4
    assert len(events_a) == expected_events_a, (
        f"expected {expected_events_a} events for handle1, got {len(events_a)}"
    )
    assert len(events_b) == expected_events_b, (
        f"expected {expected_events_b} events for both handles, got {len(events_b)}"
    )


def test_failed_exec_emits_failed_event() -> None:

    def raising_factory(*args: object, **kwargs: object) -> FakePopen:
        raise OSError("definitely-not-a-real-command-xyz-ralph")

    pm = _make_pm(sync_factory=raising_factory)
    events: list[ProcessEvent] = []
    pm.register_listener(events.append)

    with pytest.raises(OSError):
        pm.spawn(["definitely-not-a-real-command-xyz-ralph"])

    failed_events = [e for e in events if e.new_status == ProcessStatus.FAILED]
    assert len(failed_events) == 1


def test_shutdown_all_for_label_kills_only_matching() -> None:
    sync_factory = make_sync_process_factory(itertools.count(1), returncode=None)
    pm = _make_pm(sync_factory=sync_factory)

    target = pm.spawn(
        [sys.executable, "-c", "pass"],
        SpawnOptions(label="worker:target"),
    )
    bystander = pm.spawn(
        [sys.executable, "-c", "pass"],
        SpawnOptions(label="other:bystander"),
    )

    with patch("os.kill", return_value=None):
        pm.shutdown_all_for_label("worker:", grace_period_s=0)

    assert target.record.status in (ProcessStatus.KILLED, ProcessStatus.EXITED)
    assert bystander.record.status == ProcessStatus.RUNNING


def test_raising_listener_does_not_break_lifecycle() -> None:
    sync_factory = make_sync_process_factory(itertools.count(1), returncode=0)
    pm = _make_pm(sync_factory=sync_factory)
    good_events: list[ProcessEvent] = []

    def bad_listener(event: ProcessEvent) -> None:
        raise RuntimeError("listener exploded")

    pm.register_listener(bad_listener)
    pm.register_listener(good_events.append)

    handle = pm.spawn([sys.executable, "-c", "pass"])
    handle.wait(timeout=5.0)

    assert handle.record.status == ProcessStatus.EXITED
    assert len(good_events) >= 1


def test_log_events_false_suppresses_loguru_output() -> None:
    records: list[str] = []
    sink_id = logger.add(lambda msg: records.append(str(msg)), level="DEBUG", format="{message}")
    try:
        pm_silent = ProcessManager(
            policy=ProcessManagerPolicy(
                default_grace_period_s=0.1,
                kill_followup_timeout_s=0.2,
                log_events=False,
                enable_zombie_reaper=False,
            ),
            sync_process_factory=make_sync_process_factory(itertools.count(1), returncode=1),
        )
        handle = pm_silent.spawn([sys.executable, "-c", "pass"])
        handle.wait(timeout=5.0)
        pid_str = str(handle.record.pid)
        process_lines_silent = [r for r in records if "process " in r and pid_str in r]
        assert process_lines_silent == [], (
            f"Expected no process log lines with log_events=False, got: {process_lines_silent}"
        )

        records.clear()

        pm_loud = ProcessManager(
            policy=ProcessManagerPolicy(
                default_grace_period_s=0.1,
                kill_followup_timeout_s=0.2,
                log_events=True,
                enable_zombie_reaper=False,
            ),
            sync_process_factory=make_sync_process_factory(itertools.count(1), returncode=1),
        )
        handle2 = pm_loud.spawn([sys.executable, "-c", "pass"])
        handle2.wait(timeout=5.0)
        pid2_str = str(handle2.record.pid)
        process_lines_loud = [r for r in records if "process " in r and pid2_str in r]
        assert len(process_lines_loud) >= 1, (
            f"Expected process log lines with log_events=True, got none. records={records}"
        )
    finally:
        logger.remove(sink_id)


@pytest.mark.parametrize(
    ("status", "returncode", "expected_level"),
    [
        (ProcessStatus.SPAWNED, None, None),
        (ProcessStatus.RUNNING, None, None),
        (ProcessStatus.EXITED, 0, None),
        (ProcessStatus.EXITED, 1, "WARNING"),
        (ProcessStatus.KILLED, 0, None),
        (ProcessStatus.KILLED, None, "WARNING"),
        (ProcessStatus.KILLED, 137, "WARNING"),
        (ProcessStatus.FAILED, None, "ERROR"),
    ],
)
def test_process_lifecycle_events_emit_only_actionable_statuses(
    status: ProcessStatus,
    returncode: int | None,
    expected_level: str | None,
) -> None:
    levels: list[str] = []

    def capture_level(message: object) -> None:
        levels.append(str(message).strip())

    sink_id = logger.add(
        capture_level,
        level="DEBUG",
        format="{level}",
    )
    try:
        record = ProcessRecord(
            pid=123,
            pgid=123,
            command=("python", "-c", "pass"),
            cwd=None,
            started_at=datetime.now(tz=UTC),
            status=status,
            returncode=returncode,
            failure_message="spawn failed" if status == ProcessStatus.FAILED else None,
            label="test",
        )
        event = ProcessEvent(
            record=record,
            previous_status=ProcessStatus.RUNNING,
            new_status=status,
            timestamp=datetime.now(tz=UTC),
        )

        loguru_event_listener(event)

        assert levels == ([] if expected_level is None else [expected_level])
    finally:
        logger.remove(sink_id)


def test_successful_zombie_reconciliation_logs_below_warning() -> None:
    records: list[str] = []
    sink_id = logger.add(
        lambda msg: records.append(str(msg)),
        level="WARNING",
        format="{level}:{message}",
    )
    try:
        record = ProcessRecord(
            pid=123,
            pgid=123,
            command=("python", "-c", "pass"),
            cwd=None,
            started_at=datetime.now(tz=UTC),
            status=ProcessStatus.KILLED,
            returncode=0,
            cause="zombie_reconciled",
            label="test",
        )
        event = ProcessEvent(
            record=record,
            previous_status=ProcessStatus.RUNNING,
            new_status=ProcessStatus.KILLED,
            timestamp=datetime.now(tz=UTC),
        )

        loguru_event_listener(event)

        assert records == []
    finally:
        logger.remove(sink_id)


def test_regression_killed_rc_zero_any_cause_logs_below_warning() -> None:
    records: list[str] = []
    sink_id = logger.add(
        lambda msg: records.append(str(msg)),
        level="WARNING",
        format="{level}:{message}",
    )
    try:
        for non_zero_cause in ("graceful_shutdown", "operator_terminated", None):
            record = ProcessRecord(
                pid=456,
                pgid=456,
                command=("python", "-c", "pass"),
                cwd=None,
                started_at=datetime.now(tz=UTC),
                status=ProcessStatus.KILLED,
                returncode=0,
                cause=non_zero_cause,
                label="test",
            )
            event = ProcessEvent(
                record=record,
                previous_status=ProcessStatus.RUNNING,
                new_status=ProcessStatus.KILLED,
                timestamp=datetime.now(tz=UTC),
            )

            loguru_event_listener(event)

        assert records == []
    finally:
        logger.remove(sink_id)


def test_regression_killed_nonzero_returncode_still_warns() -> None:
    records: list[str] = []
    sink_id = logger.add(
        lambda msg: records.append(str(msg)),
        level="WARNING",
        format="{level}:{message}",
    )
    try:
        for nonzero_rc in (-1, 1, 137):
            record = ProcessRecord(
                pid=789,
                pgid=789,
                command=("python", "-c", "pass"),
                cwd=None,
                started_at=datetime.now(tz=UTC),
                status=ProcessStatus.KILLED,
                returncode=nonzero_rc,
                cause="graceful_shutdown",
                label="test",
            )
            event = ProcessEvent(
                record=record,
                previous_status=ProcessStatus.RUNNING,
                new_status=ProcessStatus.KILLED,
                timestamp=datetime.now(tz=UTC),
            )

            loguru_event_listener(event)

        assert len(records) == 3, (
            f"KILLED with non-zero returncode must still emit WARNING; "
            f"got {len(records)} records: {records}"
        )
    finally:
        logger.remove(sink_id)


def test_regression_killed_none_returncode_still_warns() -> None:
    records: list[str] = []
    sink_id = logger.add(
        lambda msg: records.append(str(msg)),
        level="WARNING",
        format="{level}:{message}",
    )
    try:
        record = ProcessRecord(
            pid=321,
            pgid=321,
            command=("python", "-c", "pass"),
            cwd=None,
            started_at=datetime.now(tz=UTC),
            status=ProcessStatus.KILLED,
            returncode=None,
            cause="signal_killed",
            label="test",
        )
        event = ProcessEvent(
            record=record,
            previous_status=ProcessStatus.RUNNING,
            new_status=ProcessStatus.KILLED,
            timestamp=datetime.now(tz=UTC),
        )

        loguru_event_listener(event)

        assert len(records) == 1, (
            f"KILLED with returncode=None must still emit WARNING; "
            f"got {len(records)} records: {records}"
        )
    finally:
        logger.remove(sink_id)


def test_descendant_snapshot_excludes_zombies() -> None:
    sync_factory = make_sync_process_factory(itertools.count(1), returncode=None)
    parent_pid = 1
    live_child = FakePsutilProcess(pid=1001, _running=True, _status="sleeping", _create_time=0.0)
    zombie_child = FakePsutilProcess(pid=1002, _running=True, _status="zombie", _create_time=0.0)

    class _RootWithChildren(FakePsutilProcess):
        def children(self, recursive: bool = False) -> list[FakePsutilProcess]:
            return [live_child, zombie_child]

    root = _RootWithChildren(pid=parent_pid)
    fake_psutil = FakePsutil()
    fake_psutil._processes = {
        parent_pid: root,
        1001: live_child,
        1002: zombie_child,
    }

    pm = _make_pm(sync_factory=sync_factory, psutil_mod=fake_psutil)

    handle = pm.spawn([sys.executable, "-c", "pass"])
    handle._record.pid = parent_pid

    count, oldest = handle.descendant_snapshot()

    assert count == 1, f"Expected 1 live non-zombie descendant; got {count}"
    assert oldest is not None


def test_descendant_snapshot_returns_oldest_age() -> None:
    sync_factory = make_sync_process_factory(itertools.count(1), returncode=None)

    now = _time.monotonic()
    older_age = 20.0
    newer_age = 5.0

    parent_pid = 1
    old_child = FakePsutilProcess(
        pid=2001,
        _running=True,
        _status="sleeping",
        _create_time=now - older_age,
    )
    new_child = FakePsutilProcess(
        pid=2002,
        _running=True,
        _status="running",
        _create_time=now - newer_age,
    )

    class _RootWithChildren(FakePsutilProcess):
        def children(self, recursive: bool = False) -> list[FakePsutilProcess]:
            return [old_child, new_child]

    root = _RootWithChildren(pid=parent_pid)
    fake_psutil = FakePsutil()
    fake_psutil._processes = {
        parent_pid: root,
        2001: old_child,
        2002: new_child,
    }

    pm = _make_pm(sync_factory=sync_factory, psutil_mod=fake_psutil)

    handle = pm.spawn([sys.executable, "-c", "pass"])
    handle._record.pid = parent_pid

    count, oldest = handle.descendant_snapshot()

    expected_count = 2
    min_oldest_age = older_age - 1.0
    assert count == expected_count, f"Expected {expected_count} live descendants; got {count}"
    assert oldest is not None
    assert oldest >= min_oldest_age, f"Expected oldest age >= {min_oldest_age}s; got {oldest}"


def test_has_live_descendants_excludes_zombies() -> None:
    sync_factory = make_sync_process_factory(itertools.count(1), returncode=None)

    parent_pid = 1
    zombie_child = FakePsutilProcess(pid=3001, _running=True, _status="zombie", _create_time=0.0)

    class _RootWithChildren(FakePsutilProcess):
        def children(self, recursive: bool = False) -> list[FakePsutilProcess]:
            return [zombie_child]

    root = _RootWithChildren(pid=parent_pid)
    fake_psutil = FakePsutil()
    fake_psutil._processes = {
        parent_pid: root,
        3001: zombie_child,
    }

    pm = _make_pm(sync_factory=sync_factory, psutil_mod=fake_psutil)

    handle = pm.spawn([sys.executable, "-c", "pass"])
    handle._record.pid = parent_pid

    assert handle.has_live_descendants() is False


def test_terminate_with_live_descendants() -> None:
    sync_factory = make_sync_process_factory(itertools.count(1), returncode=None)

    parent_pid = 1
    child = FakePsutilProcess(pid=4001, _running=True, _status="sleeping", _create_time=0.0)

    class _RootWithChildren(FakePsutilProcess):
        def children(self, recursive: bool = False) -> list[FakePsutilProcess]:
            return [child]

    root = _RootWithChildren(pid=parent_pid)
    fake_psutil = FakePsutil()
    fake_psutil._processes = {
        parent_pid: root,
        4001: child,
    }

    pm = _make_pm(sync_factory=sync_factory, psutil_mod=fake_psutil)

    handle = pm.spawn([sys.executable, "-c", "pass"])
    handle._record.pid = parent_pid
    assert handle.has_live_descendants() is True

    handle.terminate(grace_period_s=0.1, issuer=ProcessTerminationIssuer.OPERATOR)

    assert handle.record.status == ProcessStatus.KILLED
    assert handle.record.cause == "killed"


def test_get_record_returns_terminal_history_after_exit() -> None:
    sync_factory = make_sync_process_factory(itertools.count(1), returncode=0)
    pm = _make_pm(sync_factory=sync_factory)

    handle = pm.spawn([sys.executable, "-c", "pass"], SpawnOptions(label="phase:test"))
    pid = handle.pid
    handle.wait(timeout=5.0)

    record = pm.get_record(pid)
    assert record is not None
    assert record.status == ProcessStatus.EXITED
    assert pm.get_record(pid, include_terminal=False) is None


def test_list_records_filters_active_and_terminal_by_label_prefix() -> None:
    sync_factory = make_sync_process_factory(itertools.count(1), returncode=0)
    pm = _make_pm(sync_factory=sync_factory)

    active = pm.spawn([sys.executable, "-c", "pass"], SpawnOptions(label="phase:keep"))
    terminal = pm.spawn([sys.executable, "-c", "pass"], SpawnOptions(label="phase:done"))
    terminal.wait(timeout=5.0)

    active_records = pm.list_records(
        include_active=True,
        include_terminal=False,
        label_prefix="phase:",
    )
    terminal_records = pm.list_records(
        include_active=False,
        include_terminal=True,
        label_prefix="phase:",
    )

    assert [r.pid for r in active_records] == [active.pid]
    assert [r.pid for r in terminal_records] == [terminal.pid]


def test_terminal_history_limit_evicts_oldest_terminal_records_first() -> None:
    sync_factory = make_sync_process_factory(itertools.count(1), returncode=0)
    pm = ProcessManager(
        policy=ProcessManagerPolicy(
            default_grace_period_s=0.3,
            kill_followup_timeout_s=0.5,
            log_events=False,
            terminal_history_limit=2,
            enable_zombie_reaper=False,
        ),
        sync_process_factory=sync_factory,
        async_process_factory=make_async_process_factory(itertools.count(100)),
    )

    handles = [
        pm.spawn([sys.executable, "-c", "pass"], SpawnOptions(label=f"phase:{idx}"))
        for idx in range(3)
    ]
    for handle in handles:
        handle.wait(timeout=5.0)

    terminal_records = pm.list_records(
        include_active=False,
        include_terminal=True,
        label_prefix="phase:",
    )
    terminal_pids = [record.pid for record in terminal_records]

    assert terminal_pids == [handles[1].pid, handles[2].pid]
    assert pm.get_record(handles[0].pid) is None


def test_ec1_spawn_pid_in_list_active() -> None:
    sync_factory = make_sync_process_factory(itertools.count(1), returncode=0)
    pm = _make_pm(sync_factory=sync_factory)
    handle = pm.spawn([sys.executable, "-c", "pass"])
    active_pids = [r.pid for r in pm.list_active()]
    assert handle.record.pid in active_pids
    handle.wait(timeout=5.0)


def test_ec2_process_exits_normally() -> None:
    sync_factory = make_sync_process_factory(itertools.count(1), returncode=0)
    pm = _make_pm(sync_factory=sync_factory)
    handle = pm.spawn([sys.executable, "-c", "pass"])
    handle.wait(timeout=5.0)
    assert handle.record.status == ProcessStatus.EXITED
    assert handle.record.returncode == 0
    assert handle.record.cause == "exited"


def test_ec3_stubborn_process_sigkill_escalation() -> None:
    never_die_factory = make_sync_process_factory(itertools.count(1), returncode=-9)
    pm = _make_pm(sync_factory=never_die_factory)
    handle = pm.spawn([sys.executable, "-c", "pass"])
    handle.terminate(grace_period_s=0.1, issuer=ProcessTerminationIssuer.OPERATOR)
    assert handle.record.status == ProcessStatus.KILLED
    assert handle.record.cause == "killed"


def test_ec4_sigterm_timeout_escalates_to_sigkill() -> None:

    def stubborn_factory(command: object, opts: object) -> FakeStubbornPopen:
        del command, opts
        return FakeStubbornPopen(pid=1, final_returncode=-9)

    sink_records: list[str] = []
    sink_id = logger.add(
        lambda msg: sink_records.append(str(msg)),
        level="DEBUG",
        format="{level}:{message}",
    )
    try:
        pm = ProcessManager(
            policy=_FAST_POLICY,
            sync_process_factory=stubborn_factory,
            async_process_factory=make_async_process_factory(itertools.count(100)),
            psutil=None,
        )
        handle = pm.spawn([sys.executable, "-c", "pass"])

        handle.terminate(grace_period_s=0.01, issuer=ProcessTerminationIssuer.OPERATOR)

        assert handle.record.status == ProcessStatus.KILLED
        assert handle.record.returncode == -9
        assert handle.record.cause == "killed"
    finally:
        logger.remove(sink_id)


def test_ec9_root_only_force_kill_still_alive_raises_error() -> None:

    def immortal_factory(command: object, opts: object) -> FakeImmortalPopen:
        del command, opts
        return FakeImmortalPopen(pid=1)

    pm = ProcessManager(
        policy=_FAST_POLICY,
        sync_process_factory=immortal_factory,
        async_process_factory=make_async_process_factory(itertools.count(100)),
        psutil=None,
    )
    handle = pm.spawn([sys.executable, "-c", "pass"])

    with pytest.raises(ProcessTerminationError):
        handle.terminate(grace_period_s=0.01, issuer=ProcessTerminationIssuer.OPERATOR)

    assert handle.record.status == ProcessStatus.FAILED
    assert handle.record.cause == "termination_failed"
    assert handle.record.failure_message == "Process still alive after kill"
    assert pm.list_active() == []


@pytest.mark.asyncio
async def test_ec5_spawn_async_appears_in_list_active() -> None:
    async_factory = make_async_process_factory(itertools.count(1), returncode=0)
    pm = _make_pm(async_factory=async_factory)
    handle = await pm.spawn_async([sys.executable, "-c", "pass"])
    active_pids = [r.pid for r in pm.list_active()]
    assert handle.record.pid in active_pids
    await handle.terminate(grace_period_s=0.1, issuer=ProcessTerminationIssuer.OPERATOR)


def test_ec6_sigterm_sent_before_psutil_timeout() -> None:
    sync_factory = make_sync_process_factory(itertools.count(1), returncode=None)

    class _FakePsutilWithTrack(FakePsutil):
        terminate_called = False

        def process_from_pid(self, pid: int) -> FakePsutilProcess:
            proc = self._processes.get(pid)
            if proc is None:
                raise self.NoSuchProcess(pid)
            return proc

    fake_psutil = _FakePsutilWithTrack()
    parent_pid = 1
    child = FakePsutilProcess(pid=1001, _running=True, _status="sleeping", _create_time=0.0)

    class _RootWithChildren(FakePsutilProcess):
        def children(self, recursive: bool = False) -> list[FakePsutilProcess]:
            return [child]

    root = _RootWithChildren(pid=parent_pid)
    fake_psutil._processes = {parent_pid: root, 1001: child}

    pm = _make_pm(sync_factory=sync_factory, psutil_mod=fake_psutil)
    handle = pm.spawn([sys.executable, "-c", "pass"])
    handle._record.pid = parent_pid
    handle.terminate(grace_period_s=0.5, issuer=ProcessTerminationIssuer.OPERATOR)
    assert handle.record.status == ProcessStatus.KILLED


@pytest.mark.asyncio
async def test_ec7_async_shutdown_all_with_psutil_escalates() -> None:
    async_factory = make_async_process_factory(itertools.count(1), returncode=None)
    fake_psutil = FakePsutil()
    parent_pid = 1
    child = FakePsutilProcess(pid=1001, _running=True, _status="sleeping", _create_time=0.0)

    class _RootWithChildren(FakePsutilProcess):
        def children(self, recursive: bool = False) -> list[FakePsutilProcess]:
            return [child]

    root = _RootWithChildren(pid=parent_pid)
    fake_psutil._processes = {parent_pid: root, 1001: child}

    pm = ProcessManager(
        policy=_FAST_POLICY,
        sync_process_factory=make_sync_process_factory(itertools.count(1)),
        async_process_factory=async_factory,
        psutil=fake_psutil,
    )
    handle = await pm.spawn_async([sys.executable, "-c", "pass"])
    handle._record.pid = parent_pid
    spawned_pid = handle.record.pid

    pm.shutdown_all(grace_period_s=0.1)
    records = pm.list_records(include_active=False, include_terminal=True)
    assert any(r.pid == spawned_pid and r.status == ProcessStatus.KILLED for r in records)


def test_ec8_concurrent_shutdown_all_single_event() -> None:
    sync_factory = make_sync_process_factory(itertools.count(1), returncode=None)
    pm = _make_pm(sync_factory=sync_factory)

    terminal_events: list[ProcessEvent] = []
    pm.register_listener(terminal_events.append)

    handle = pm.spawn([sys.executable, "-c", "pass"])
    spawned_pid = handle.record.pid

    barrier = threading.Barrier(2)

    def _shutdown() -> None:
        barrier.wait(timeout=5.0)
        pm.shutdown_all(grace_period_s=0)

    t1 = threading.Thread(target=_shutdown)
    t2 = threading.Thread(target=_shutdown)
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    records = pm.list_records(include_active=False, include_terminal=True)
    assert len(records) == 1
    assert len(pm.list_active()) == 0
    terminal_for_pid = [
        e
        for e in terminal_events
        if e.new_status
        in (
            ProcessStatus.EXITED,
            ProcessStatus.KILLED,
            ProcessStatus.FAILED,
        )
        and e.record.pid == spawned_pid
    ]
    assert len(terminal_for_pid) == 1, f"Expected 1 terminal event, got {len(terminal_for_pid)}"


def test_ec10_process_termination_error_when_kill_fails() -> None:
    sync_factory = make_sync_process_factory(itertools.count(1), returncode=None)

    parent_pid = 1

    class _RootStubborn(FakePsutilProcess):
        def children(self, recursive: bool = False) -> list[FakePsutilProcess]:
            return []

        def terminate(self, **_kwargs: object) -> None:
            pass

        def kill(self, **_kwargs: object) -> None:
            pass

    root = _RootStubborn(pid=parent_pid)
    fake_psutil = FakePsutil()
    fake_psutil._processes = {parent_pid: root}

    pm = _make_pm(sync_factory=sync_factory, psutil_mod=fake_psutil)
    handle = pm.spawn([sys.executable, "-c", "pass"])
    handle._record.pid = parent_pid
    handle.record_terminal_reason("operator_cancellation")
    with pytest.raises(ProcessTerminationError):
        handle.terminate(grace_period_s=0.0, issuer=ProcessTerminationIssuer.OPERATOR)

    outcomes = pm.list_termination_outcomes()[handle.pid]
    stages = [outcome["stage"] for outcome in outcomes]
    assert stages.index("terminal_reason") < stages.index("force_kill")
    assert outcomes[stages.index("terminal_reason")]["outcome"] == "operator_cancellation"


def test_ec13_shutdown_all_for_label_only_matching() -> None:
    sync_factory = make_sync_process_factory(itertools.count(1), returncode=None)
    pm = _make_pm(sync_factory=sync_factory)

    target = pm.spawn(
        [sys.executable, "-c", "pass"],
        SpawnOptions(label="worker:target"),
    )
    bystander = pm.spawn(
        [sys.executable, "-c", "pass"],
        SpawnOptions(label="other:bystander"),
    )

    with patch("os.kill", return_value=None):
        pm.shutdown_all_for_label("worker:", grace_period_s=0)

    assert target.record.status in (ProcessStatus.KILLED, ProcessStatus.EXITED)
    assert bystander.record.status == ProcessStatus.RUNNING


def test_ec19_exit_record_accessible_via_public_api() -> None:
    sync_factory = make_sync_process_factory(itertools.count(1), returncode=0)
    pm = _make_pm(sync_factory=sync_factory)

    handle = pm.spawn([sys.executable, "-c", "pass"])
    handle.wait(timeout=5.0)

    records = pm.list_records(include_active=False, include_terminal=True)
    exited_records = [r for r in records if r.status == ProcessStatus.EXITED]
    assert len(exited_records) >= 1
    assert any(r.pid == handle.record.pid for r in exited_records)
    record = pm.get_record(handle.record.pid, include_terminal=True)
    assert record is not None
    assert record.status == ProcessStatus.EXITED


@pytest.mark.asyncio
async def test_ec21_shutdown_all_terminates_async_process() -> None:
    async_factory = make_async_process_factory(itertools.count(1), returncode=None)
    pm = _make_pm(async_factory=async_factory, psutil_mod=FakePsutil())

    handle = await pm.spawn_async([sys.executable, "-c", "pass"])
    spawned_pid = handle.record.pid
    pm.shutdown_all(grace_period_s=0.1)

    records = pm.list_records(include_active=False, include_terminal=True)
    assert any(r.pid == spawned_pid and r.status == ProcessStatus.KILLED for r in records)


@pytest.mark.asyncio
async def test_ec_async_live_process_still_alive_after_kill() -> None:
    async_factory = make_async_process_factory(itertools.count(1), returncode=None)
    pm = ProcessManager(
        policy=_FAST_POLICY,
        sync_process_factory=make_sync_process_factory(itertools.count(1)),
        async_process_factory=async_factory,
        psutil=None,
    )

    await pm.spawn_async([sys.executable, "-c", "pass"])

    def fake_kill(pid: int, sig: int) -> None:
        if sig == 0:
            return
        raise ProcessLookupError(pid, 0)

    with patch.object(os, "kill", fake_kill), pytest.raises(ProcessTerminationError):
        pm.shutdown_all(grace_period_s=0.1)


@pytest.mark.asyncio
async def test_ec_async_gone_process_already_gone_after_kill() -> None:
    async_factory = make_async_process_factory(itertools.count(1), returncode=None)
    pm = ProcessManager(
        policy=_FAST_POLICY,
        sync_process_factory=make_sync_process_factory(itertools.count(1)),
        async_process_factory=async_factory,
        psutil=None,
    )

    handle = await pm.spawn_async([sys.executable, "-c", "pass"])
    spawned_pid = handle.record.pid

    def kill_raises_lookup(pid: int, sig: int) -> None:
        raise ProcessLookupError(pid, sig)

    with patch.object(os, "kill", side_effect=kill_raises_lookup):
        pm.shutdown_all(grace_period_s=0.1)
    record = pm.get_record(spawned_pid, include_terminal=True)
    assert record is not None
    assert record.status == ProcessStatus.KILLED


def test_cat_mcp_spawn_with_mcp_server_label() -> None:
    sync_factory = make_sync_process_factory(itertools.count(1), returncode=None)
    pm = _make_pm(sync_factory=sync_factory)

    handle = pm.spawn(
        [sys.executable, "-c", "pass"],
        SpawnOptions(label="phase:development:mcp-server"),
    )
    spawned_pid = handle.record.pid
    active_pids = [r.pid for r in pm.list_active()]
    assert spawned_pid in active_pids

    pm.shutdown_all(grace_period_s=0.1)
    records = pm.list_records(include_active=False, include_terminal=True)
    assert any(r.pid == spawned_pid for r in records)


def test_cat_exec_spawn_with_exec_label() -> None:
    sync_factory = make_sync_process_factory(itertools.count(1), returncode=None)
    pm = _make_pm(sync_factory=sync_factory)

    handle = pm.spawn(
        [sys.executable, "-c", "pass"],
        SpawnOptions(label="mcp-exec:python"),
    )
    spawned_pid = handle.record.pid
    active_pids = [r.pid for r in pm.list_active()]
    assert spawned_pid in active_pids

    handle.terminate(grace_period_s=0.1, issuer=ProcessTerminationIssuer.OPERATOR)

    assert handle.record.status == ProcessStatus.KILLED


@pytest.mark.asyncio
async def test_cat_agent_pm_spawn_async_with_agent_label() -> None:
    async_factory = make_async_process_factory(itertools.count(1), returncode=None)
    pm = _make_pm(async_factory=async_factory, psutil_mod=FakePsutil())

    handle = await pm.spawn_async(
        [sys.executable, "-c", "pass"],
        SpawnOptions(label="agent:development:unit-1:root"),
    )
    spawned_pid = handle.record.pid
    active_pids = [r.pid for r in pm.list_active()]
    assert spawned_pid in active_pids

    pm.shutdown_all(grace_period_s=0.1)

    records = pm.list_records(include_active=False, include_terminal=True)
    assert any(r.pid == spawned_pid and r.status == ProcessStatus.KILLED for r in records)


def _publish_named_process_manager_regressions() -> None:
    names = (
        "EC4_sigterm_timeout_escalates_to_sigkill",
        "EC9_root_only_force_kill_still_alive_raises_error",
        "EC7_async_shutdown_all_with_psutil_escalates",
        "EC10_process_termination_error_when_kill_fails",
        "EC21_shutdown_all_terminates_async_process",
        "EC_ASYNC_LIVE_process_still_alive_after_kill",
        "EC_ASYNC_GONE_process_already_gone_after_kill",
        "CAT_MCP_spawn_with_mcp_server_label",
        "CAT_EXEC_spawn_with_exec_label",
        "CAT_AGENT_PM_spawn_async_with_agent_label",
    )
    for name in names:
        globals()[f"test_{name}"] = globals()[f"test_{name.lower()}"]


def test_atexit_shutdown_skips_when_no_instance() -> None:
    reset_process_manager()
    shutdown_process_manager()


def test_atexit_shutdown_calls_pm_shutdown_all() -> None:
    captured: dict[str, object] = {}

    class _FakePM:
        def shutdown_all(self, *, grace_period_s: float | None = None) -> None:
            captured["grace"] = grace_period_s

    install_process_manager(_FakePM())
    try:
        shutdown_process_manager()
        assert captured.get("grace") == 0.5
    finally:
        reset_process_manager()


def test_atexit_shutdown_swallows_baseexception() -> None:

    class _ExplodingPM:
        def shutdown_all(self, *, grace_period_s: float | None = None) -> None:
            del grace_period_s
            raise RuntimeError("shutdown-failed")

    install_process_manager(_ExplodingPM())
    try:
        shutdown_process_manager()
    finally:
        reset_process_manager()


def test_get_process_manager_registers_atexit_exactly_once() -> None:
    register_calls: list[object] = []
    original_register = atexit.register

    def _spy_register(fn: object) -> object:
        register_calls.append(fn)
        return original_register(fn)

    reset_process_manager(reset_atexit_registration=True)
    try:
        with patch("atexit.register", side_effect=_spy_register):
            get_process_manager()
            get_process_manager()
            get_process_manager()
    finally:
        reset_process_manager()

    assert len(register_calls) == 1, f"Expected 1 atexit.register call, got {len(register_calls)}"


def test_process_phase_scope_tears_down_labeled_processes() -> None:
    sync_factory = make_sync_process_factory(itertools.count(1), returncode=None)
    pm = install_process_manager(
        ProcessManager(
            policy=ProcessManagerPolicy(
                default_grace_period_s=0.1,
                kill_followup_timeout_s=0.1,
                log_events=False,
                enable_zombie_reaper=False,
            ),
            sync_process_factory=sync_factory,
            async_process_factory=make_sync_process_factory(itertools.count(100)),
        )
    )
    handle = pm.spawn(
        [sys.executable, "-c", "pass"],
        SpawnOptions(label="phase:audit"),
    )

    with process_phase_scope("audit"):
        pass

    assert handle.record.status == ProcessStatus.KILLED


def test_process_phase_scope_logs_and_reraises_on_termination_error() -> None:

    class _ExplodingPM:
        policy = ProcessManagerPolicy(
            default_grace_period_s=0.1,
            kill_followup_timeout_s=0.1,
            log_events=False,
            enable_zombie_reaper=False,
        )

        def shutdown_all_for_label(
            self,
            label: str,
            *,
            grace_period_s: float | None = None,
        ) -> None:
            del label, grace_period_s
            raise ProcessTerminationError(
                1234,
                1234,
                stage="force_kill",
                reason="cleanup failed",
            )

    install_process_manager(_ExplodingPM())
    try:
        with pytest.raises(ProcessTerminationError), process_phase_scope("audit"):
            pass
    finally:
        reset_process_manager()


@pytest.mark.asyncio
async def test_pm_terminate_routes_async_handle_to_escalation() -> None:
    target_pid_ref: list[int] = [0]
    first_seen: list[bool] = [False]

    def _fake_verify(
        p: int,
        *,
        psutil_mod: object = None,
    ) -> LivenessResult:
        del psutil_mod
        if p == target_pid_ref[0] and first_seen[0]:
            return LivenessResult.GONE
        if p == target_pid_ref[0]:
            first_seen[0] = True
        return LivenessResult.ALIVE

    created: list[_RecordingAsyncProc] = []
    pm = ProcessManager(
        policy=_FAST_POLICY,
        sync_process_factory=make_sync_process_factory(itertools.count(100)),
        async_process_factory=_recording_async_factory(created),
        psutil=None,
        liveness_verifier=_fake_verify,
    )

    handle = await pm.spawn_async(
        [sys.executable, "-c", "pass"],
        SpawnOptions(stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE),
    )
    assert handle.record.status == ProcessStatus.RUNNING
    target_pid_ref[0] = handle.pid
    first_seen[0] = False

    handle.record_terminal_reason("operator_cancellation")
    pm.terminate(handle, grace_period_s=0.0, issuer=ProcessTerminationIssuer.OPERATOR)

    fake_proc = created[0]
    assert fake_proc.termination_calls >= 1, (
        "pm.terminate on an async handle must drive proc.terminate()/kill()"
    )
    assert handle.record.status == ProcessStatus.KILLED


@pytest.mark.asyncio
async def test_pm_terminate_async_handle_is_idempotent() -> None:
    target_pid_ref: list[int] = [0]
    first_seen: list[bool] = [False]

    def _fake_verify(
        p: int,
        *,
        psutil_mod: object = None,
    ) -> LivenessResult:
        del psutil_mod
        if p == target_pid_ref[0] and first_seen[0]:
            return LivenessResult.GONE
        if p == target_pid_ref[0]:
            first_seen[0] = True
        return LivenessResult.ALIVE

    created: list[_RecordingAsyncProc] = []
    pm = ProcessManager(
        policy=_FAST_POLICY,
        sync_process_factory=make_sync_process_factory(itertools.count(100)),
        async_process_factory=_recording_async_factory(created),
        psutil=None,
        liveness_verifier=_fake_verify,
    )

    handle = await pm.spawn_async(
        [sys.executable, "-c", "pass"],
        SpawnOptions(stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE),
    )
    fake_proc = created[0]
    target_pid_ref[0] = handle.pid
    first_seen[0] = False

    pm.terminate(handle, grace_period_s=0.0, issuer=ProcessTerminationIssuer.OPERATOR)
    terminated_count_after_first = fake_proc.termination_calls
    first_cause = handle.record.cause
    first_status = handle.record.status
    assert first_status == ProcessStatus.KILLED

    pm.terminate(handle, grace_period_s=0.0, issuer=ProcessTerminationIssuer.OPERATOR)

    terminated_count_after_second = fake_proc.termination_calls
    assert handle.record.status == first_status
    assert handle.record.cause == first_cause
    assert terminated_count_after_second == terminated_count_after_first, (
        "pm.terminate must not re-terminate an already-terminal async handle"
    )


_publish_named_process_manager_regressions()


def test_shutdown_all_continues_when_first_record_raises_termination_error() -> None:
    alive_pids = {1, 2}

    def _fake_os_kill(pid: int, sig: int) -> None:
        if sig == 0:
            if pid in alive_pids:
                return
            raise ProcessLookupError(pid, sig)
        if pid in alive_pids:
            return
        raise ProcessLookupError(pid, sig)

    call_count = {"value": 0}

    def _alternating_factory(command: object, opts: object) -> object:
        del command, opts
        call_count["value"] += 1
        if call_count["value"] == 1:
            return FakeImmortalPopen(pid=1)
        return FakePopen(pid=2, state=ProcessState())

    pm = ProcessManager(
        policy=_FAST_POLICY,
        sync_process_factory=_alternating_factory,
        async_process_factory=make_async_process_factory(itertools.count(100)),
        psutil=None,
    )

    first = pm.spawn([sys.executable, "-c", "pass"])
    second = pm.spawn([sys.executable, "-c", "pass"])

    with (
        patch("os.kill", side_effect=_fake_os_kill),
        pytest.raises(ProcessTerminationError),
    ):
        pm.shutdown_all(grace_period_s=0.01)

    assert first.record.status == ProcessStatus.FAILED
    assert second.record.status in (ProcessStatus.KILLED, ProcessStatus.EXITED), (
        f"second sibling stranded with status={second.record.status}"
    )
    assert not pm.list_active()


def test_reset_process_manager_shuts_down_prior_instance() -> None:
    alive_pids = {1}

    def _fake_os_kill(pid: int, sig: int) -> None:
        if sig == 0:
            if pid in alive_pids:
                return
            raise ProcessLookupError(pid, sig)
        if pid in alive_pids:
            return
        raise ProcessLookupError(pid, sig)

    sync_factory = make_sync_process_factory(itertools.count(1), returncode=None)
    pm = ProcessManager(
        policy=_FAST_POLICY,
        sync_process_factory=sync_factory,
        async_process_factory=make_async_process_factory(itertools.count(100)),
        psutil=None,
    )
    install_process_manager(pm)

    singleton = get_process_manager()
    assert singleton is pm
    handle = pm.spawn([sys.executable, "-c", "pass"])
    assert handle.record.status == ProcessStatus.RUNNING

    with patch("os.kill", side_effect=_fake_os_kill):
        reset_process_manager()

    assert handle.record.status in (ProcessStatus.KILLED, ProcessStatus.EXITED)
    assert not pm.list_active()
    assert get_process_manager() is not pm


def test_reset_process_manager_swallows_exceptions_during_shutdown() -> None:

    class _RaisingPm:
        def shutdown_all(self, *, grace_period_s: float | None = None) -> None:
            raise RuntimeError("boom")

    raising = _RaisingPm()
    install_process_manager(raising)

    reset_process_manager()
    assert get_process_manager() is not raising


def test_shutdown_all_stops_zombie_reaper_even_when_loop_raises() -> None:
    alive_pids = {1}

    def _fake_os_kill(pid: int, sig: int) -> None:
        if sig == 0:
            if pid in alive_pids:
                return
            raise ProcessLookupError(pid, sig)
        if pid in alive_pids:
            return
        raise ProcessLookupError(pid, sig)

    pm = ProcessManager(
        policy=ProcessManagerPolicy(
            default_grace_period_s=0.01,
            kill_followup_timeout_s=0.01,
            log_events=False,
            enable_zombie_reaper=True,
        ),
        sync_process_factory=lambda *a, **kw: FakeImmortalPopen(pid=1),
        async_process_factory=make_async_process_factory(itertools.count(100)),
        psutil=None,
    )

    pm.spawn([sys.executable, "-c", "pass"])

    stop_calls: list[None] = []

    def _stop_recorder(_self: object = None) -> None:
        stop_calls.append(None)

    with (
        patch("os.kill", side_effect=_fake_os_kill),
        patch.object(ProcessManager, "_stop_zombie_reaper", _stop_recorder),
        pytest.raises(ProcessTerminationError),
    ):
        pm.shutdown_all(grace_period_s=0.0)

    assert len(stop_calls) == 1, (
        f"_stop_zombie_reaper must be invoked once even when the loop body raises; got {stop_calls}"
    )


def test_shutdown_all_for_label_continues_when_first_record_raises() -> None:
    alive_pids = {1, 2}

    def _fake_os_kill(pid: int, sig: int) -> None:
        if sig == 0:
            if pid in alive_pids:
                return
            raise ProcessLookupError(pid, sig)
        if pid in alive_pids:
            return
        raise ProcessLookupError(pid, sig)

    call_count = {"value": 0}

    def _alternating_factory(command: object, opts: object) -> object:
        del command, opts
        call_count["value"] += 1
        if call_count["value"] == 1:
            return FakeImmortalPopen(pid=1)
        return FakePopen(pid=2, state=ProcessState())

    pm = ProcessManager(
        policy=_FAST_POLICY,
        sync_process_factory=_alternating_factory,
        async_process_factory=make_async_process_factory(itertools.count(100)),
        psutil=None,
    )

    first = pm.spawn([sys.executable, "-c", "pass"], SpawnOptions(label="phase:dev"))
    second = pm.spawn([sys.executable, "-c", "pass"], SpawnOptions(label="phase:dev"))

    with (
        patch("os.kill", side_effect=_fake_os_kill),
        pytest.raises(ProcessTerminationError),
    ):
        pm.shutdown_all_for_label("phase:dev", grace_period_s=0.01)

    assert first.record.status == ProcessStatus.FAILED
    assert second.record.status in (ProcessStatus.KILLED, ProcessStatus.EXITED)


def test_process_failure_identity_regression_exit_log_names_child_safely() -> None:
    messages: list[str] = []
    sink_id = logger.add(
        lambda msg: messages.append(str(msg)),
        level="WARNING",
        format="{message}",
    )
    try:
        record = ProcessRecord(
            pid=4242,
            pgid=4242,
            command=("git", "rebase", "--continue", "--token=SUPERSECRETARGVALUE"),
            cwd=None,
            started_at=datetime.now(tz=UTC),
            status=ProcessStatus.EXITED,
            returncode=1,
            label="pending-merge:identity",
        )
        event = ProcessEvent(
            record=record,
            previous_status=ProcessStatus.RUNNING,
            new_status=ProcessStatus.EXITED,
            timestamp=datetime.now(tz=UTC),
        )

        loguru_event_listener(event)

        assert len(messages) == 1, f"expected one warning line, got {messages}"
        message = messages[0]
        assert "pending-merge:identity" in message, (
            f"failed-child line must name the child label; got {message!r}"
        )
        assert "'git'" in message, (
            f"failed-child line must name the redacted executable; got {message!r}"
        )
        assert "SUPERSECRETARGVALUE" not in message, (
            f"failed-child line must not expose argument values; got {message!r}"
        )
        assert "--token=SUPERSECRETARGVALUE" not in message, (
            f"failed-child line must not expose argument values; got {message!r}"
        )
    finally:
        logger.remove(sink_id)


def test_process_failure_identity_regression_spawn_log_names_child_safely() -> None:
    messages: list[str] = []
    sink_id = logger.add(
        lambda msg: messages.append(str(msg)),
        level="WARNING",
        format="{message}",
    )
    try:
        record = ProcessRecord(
            pid=4343,
            pgid=4343,
            command=("git", "rebase", "--continue", "--token=SUPERSECRETARGVALUE"),
            cwd=None,
            started_at=datetime.now(tz=UTC),
            status=ProcessStatus.FAILED,
            returncode=None,
            failure_message="resolver launch refused before start",
            label="pending-merge:identity",
        )
        event = ProcessEvent(
            record=record,
            previous_status=ProcessStatus.RUNNING,
            new_status=ProcessStatus.FAILED,
            timestamp=datetime.now(tz=UTC),
        )

        loguru_event_listener(event)

        assert len(messages) == 1, f"expected one error line, got {messages}"
        message = messages[0]
        assert "pending-merge:identity" in message, (
            f"failed-child line must name the child label; got {message!r}"
        )
        assert "'git'" in message, (
            f"failed-child line must name the redacted executable; got {message!r}"
        )
        assert "SUPERSECRETARGVALUE" not in message, (
            f"failed-child line must not expose argument values; got {message!r}"
        )
    finally:
        logger.remove(sink_id)
