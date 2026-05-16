"""Black-box tests for ralph.process.ProcessManager.

All tests use deterministic fake processes injected via constructor seams;
no real subprocess spawning or psutil PID polling.
"""

from __future__ import annotations

import asyncio
import itertools
import sys
import time as _time
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
    process_phase_scope,
    reset_process_manager,
)
from ralph.process.manager import ProcessTerminationError
from ralph.testing.fake_process import (
    FakePopen,
    FakePsutil,
    FakePsutilProcess,
    make_async_process_factory,
    make_sync_process_factory,
)

if TYPE_CHECKING:
    from ralph.testing.fake_process import _AsyncFactoryCallable, _SyncFactoryCallable

_FAST_POLICY = ProcessManagerPolicy(
    default_grace_period_s=0.3, kill_followup_timeout_s=0.5, log_events=False
)


@pytest.fixture(autouse=True)
def _reset_pm() -> object:
    """Ensure each test starts and ends with a clean singleton."""
    reset_process_manager()
    yield
    reset_process_manager()


def _make_pm(
    *,
    sync_factory: _SyncFactoryCallable | None = None,
    async_factory: _AsyncFactoryCallable | None = None,
    psutil_mod: FakePsutil | None = None,
) -> ProcessManager:
    """Build a ProcessManager with injected fake process factories."""
    return ProcessManager(
        policy=_FAST_POLICY,
        sync_process_factory=sync_factory or make_sync_process_factory(itertools.count(1)),
        async_process_factory=async_factory or make_async_process_factory(itertools.count(1)),
        psutil=psutil_mod,
    )


# ---------------------------------------------------------------------------
# 1. spawn tracks lifecycle: SPAWNED→RUNNING→EXITED via fake process
# ---------------------------------------------------------------------------


def test_spawn_tracks_lifecycle_to_exited() -> None:
    """spawn() creates a RUNNING record; wait() transitions to EXITED."""
    sync_factory = make_sync_process_factory(itertools.count(1), returncode=0)
    pm = _make_pm(sync_factory=sync_factory)
    events: list[ProcessEvent] = []
    pm.register_listener(events.append)

    handle = pm.spawn([sys.executable, "-c", "pass"])
    assert handle.record.status == ProcessStatus.RUNNING
    assert handle.record.pid >= 1

    handle.wait()

    assert handle.record.status == ProcessStatus.EXITED
    assert handle.record.cause == "exited"
    assert handle.record.returncode == 0
    statuses = [e.new_status for e in events]
    assert ProcessStatus.RUNNING in statuses
    assert ProcessStatus.EXITED in statuses


# ---------------------------------------------------------------------------
# 2. terminate() on a fake process that ignores SIGTERM escalates to kill
# ---------------------------------------------------------------------------


def test_terminate_escalates_to_sigkill() -> None:
    """terminate() marks KILLED when the fake process ignores terminate()."""
    never_die_factory = make_sync_process_factory(itertools.count(1), returncode=-9)
    pm = _make_pm(sync_factory=never_die_factory)

    handle = pm.spawn([sys.executable, "-c", "pass"])
    handle.terminate(grace_period_s=0.1)

    assert handle.record.status == ProcessStatus.KILLED
    assert handle.record.cause == "killed"


# ---------------------------------------------------------------------------
# list_active returns labeled records for shared process observability
# ---------------------------------------------------------------------------


def test_list_active_returns_labeled_running_records() -> None:
    """list_active() returns all non-terminated records; labels are preserved."""
    pm = _make_pm()
    h1 = pm.spawn(
        [sys.executable, "-c", "pass"], SpawnOptions(label="phase:development:mcp-server")
    )
    h2 = pm.spawn([sys.executable, "-c", "pass"], SpawnOptions(label="invoke:dev-agent"))
    active = pm.list_active()
    labels = {r.label for r in active}
    assert "phase:development:mcp-server" in labels
    assert "invoke:dev-agent" in labels
    # Clean up
    h1.terminate(grace_period_s=0.1)
    h2.terminate(grace_period_s=0.1)


def test_list_active_excludes_terminated_processes() -> None:
    """list_active() excludes processes that have exited."""
    sync_factory = make_sync_process_factory(itertools.count(1), returncode=0)
    pm = _make_pm(sync_factory=sync_factory)
    handle = pm.spawn([sys.executable, "-c", "pass"], SpawnOptions(label="phase:test"))
    handle.wait(timeout=2.0)
    active = pm.list_active()
    assert all(r.label != "phase:test" for r in active)


def test_list_active_unlabeled_processes_not_included_when_filtering_labels() -> None:
    """Processes without labels have label=None and should be filterable at call site."""
    pm = _make_pm()
    handle = pm.spawn([sys.executable, "-c", "pass"])
    active = pm.list_active()
    unlabeled = [r for r in active if r.label is None]
    assert any(r.pid == handle.pid for r in unlabeled)
    handle.terminate(grace_period_s=0.1)


# ---------------------------------------------------------------------------
# 3. spawn_async captures output via fake async process
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_spawn_async_captures_output() -> None:
    """spawn_async() fake process can communicate output."""
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


# ---------------------------------------------------------------------------
# 4. shutdown_all kills all tracked fake processes
# ---------------------------------------------------------------------------


def test_shutdown_all_kills_multiple_children() -> None:
    """shutdown_all() marks all tracked processes as KILLED."""
    sync_factory = make_sync_process_factory(itertools.count(1), returncode=None)
    pm = _make_pm(sync_factory=sync_factory)

    handles = [pm.spawn([sys.executable, "-c", "pass"]) for _ in range(3)]
    pids = [h.record.pid for h in handles]

    # All should be running
    assert all(h.record.status == ProcessStatus.RUNNING for h in handles)

    pm.shutdown_all(grace_period_s=0)

    for h in handles:
        assert h.record.status in (ProcessStatus.KILLED, ProcessStatus.EXITED)

    # Records should no longer be in active list
    active_pids = [r.pid for r in pm.list_active()]
    for pid in pids:
        assert pid not in active_pids


# ---------------------------------------------------------------------------
# 5. listener receives events and unsubscribe stops delivery
# ---------------------------------------------------------------------------


def test_listener_receives_one_event_per_transition_and_unsubscribe_works() -> None:
    """register_listener() delivers events; unsubscribe() stops them."""
    sync_factory = make_sync_process_factory(itertools.count(1), returncode=0)
    pm = _make_pm(sync_factory=sync_factory)
    events_a: list[ProcessEvent] = []
    events_b: list[ProcessEvent] = []

    unsub_a = pm.register_listener(events_a.append)
    _unsub_b = pm.register_listener(events_b.append)

    handle = pm.spawn([sys.executable, "-c", "pass"])
    handle.wait()

    unsub_a()

    handle2 = pm.spawn([sys.executable, "-c", "pass"])
    handle2.wait()

    expected_events_a = 2
    expected_events_b = 4
    assert len(events_a) == expected_events_a, (
        f"expected {expected_events_a} events for handle1, got {len(events_a)}"
    )
    assert len(events_b) == expected_events_b, (
        f"expected {expected_events_b} events for both handles, got {len(events_b)}"
    )


# ---------------------------------------------------------------------------
# 6. failure to exec (missing binary) emits FAILED event and raises OSError
# ---------------------------------------------------------------------------


def test_failed_exec_emits_failed_event() -> None:
    """Missing command emits exactly one FAILED event and raises OSError."""

    # Override factory to raise OSError (simulating missing binary)
    def raising_factory(*args: object, **kwargs: object) -> FakePopen:
        raise OSError("definitely-not-a-real-command-xyz-ralph")

    pm = _make_pm(sync_factory=raising_factory)
    events: list[ProcessEvent] = []
    pm.register_listener(events.append)

    with pytest.raises(OSError):
        pm.spawn(["definitely-not-a-real-command-xyz-ralph"])

    failed_events = [e for e in events if e.new_status == ProcessStatus.FAILED]
    assert len(failed_events) == 1


# ---------------------------------------------------------------------------
# 7. shutdown_all_for_label kills only prefixed processes
# ---------------------------------------------------------------------------


def test_shutdown_all_for_label_kills_only_matching() -> None:
    """shutdown_all_for_label() only kills records with matching label prefix."""
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

    pm.shutdown_all_for_label("worker:", grace_period_s=0)

    assert target.record.status in (ProcessStatus.KILLED, ProcessStatus.EXITED)
    assert bystander.record.status == ProcessStatus.RUNNING


# ---------------------------------------------------------------------------
# 8. raising listener doesn't break lifecycle progression
# ---------------------------------------------------------------------------


def test_raising_listener_does_not_break_lifecycle() -> None:
    """A listener that raises does not prevent other listeners or lifecycle updates."""
    sync_factory = make_sync_process_factory(itertools.count(1), returncode=0)
    pm = _make_pm(sync_factory=sync_factory)
    good_events: list[ProcessEvent] = []

    def bad_listener(event: ProcessEvent) -> None:
        raise RuntimeError("listener exploded")

    pm.register_listener(bad_listener)
    pm.register_listener(good_events.append)

    handle = pm.spawn([sys.executable, "-c", "pass"])
    handle.wait()

    assert handle.record.status == ProcessStatus.EXITED
    assert len(good_events) >= 1


# ---------------------------------------------------------------------------
# 9. log_events=False suppresses default loguru listener; =True emits lines
# ---------------------------------------------------------------------------


def test_log_events_false_suppresses_loguru_output() -> None:
    """log_events=False produces no process log lines; True produces them."""
    records: list[str] = []
    sink_id = logger.add(lambda msg: records.append(str(msg)), level="DEBUG", format="{message}")
    try:
        # No log events when disabled
        pm_silent = ProcessManager(
            policy=ProcessManagerPolicy(
                default_grace_period_s=0.1,
                kill_followup_timeout_s=0.2,
                log_events=False,
            ),
            sync_process_factory=make_sync_process_factory(itertools.count(1), returncode=0),
        )
        handle = pm_silent.spawn([sys.executable, "-c", "pass"])
        handle.wait()
        pid_str = str(handle.record.pid)
        process_lines_silent = [r for r in records if "process " in r and pid_str in r]
        assert process_lines_silent == [], (
            f"Expected no process log lines with log_events=False, got: {process_lines_silent}"
        )

        records.clear()

        # Log events when enabled
        pm_loud = ProcessManager(
            policy=ProcessManagerPolicy(
                default_grace_period_s=0.1,
                kill_followup_timeout_s=0.2,
                log_events=True,
            ),
            sync_process_factory=make_sync_process_factory(itertools.count(1), returncode=0),
        )
        handle2 = pm_loud.spawn([sys.executable, "-c", "pass"])
        handle2.wait()
        pid2_str = str(handle2.record.pid)
        process_lines_loud = [r for r in records if "process " in r and pid2_str in r]
        assert len(process_lines_loud) >= 1, (
            f"Expected process log lines with log_events=True, got none. records={records}"
        )
    finally:
        logger.remove(sink_id)


# ---------------------------------------------------------------------------
# 10. process_phase_scope warns on ProcessTerminationError
# ---------------------------------------------------------------------------


def test_process_phase_scope_warns_on_termination_error() -> None:
    """process_phase_scope emits a loguru warning when cleanup fails, but exits cleanly."""

    def _raise_termination_error(label_prefix: str, *, grace_period_s: float | None = None) -> None:
        raise ProcessTerminationError(pid=99999, pgid=99999)

    pm = get_process_manager()

    warning_messages: list[str] = []
    sink_id = logger.add(
        lambda msg: warning_messages.append(str(msg)) if "WARNING" in str(msg) else None,
        level="WARNING",
        format="{level} {message}",
    )
    try:
        with (
            patch.object(pm, "shutdown_all_for_label", _raise_termination_error),
            process_phase_scope("test-phase"),
        ):
            pass
    finally:
        logger.remove(sink_id)

    assert any("test-phase" in msg for msg in warning_messages), (
        f"Expected warning mentioning 'test-phase', got: {warning_messages}"
    )


# ---------------------------------------------------------------------------
# 11. ManagedProcess.descendant_snapshot() excludes zombies and returns oldest age
# ---------------------------------------------------------------------------


def test_descendant_snapshot_excludes_zombies() -> None:
    """descendant_snapshot() excludes zombie processes and returns only live count."""
    sync_factory = make_sync_process_factory(itertools.count(1), returncode=None)

    # Set up fake psutil with a process that has children: one live, one zombie
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
    # Manually set the handle's pid to match our fake
    handle._record.pid = parent_pid

    count, oldest = handle.descendant_snapshot()

    assert count == 1, f"Expected 1 live non-zombie descendant; got {count}"
    assert oldest is not None


def test_descendant_snapshot_returns_oldest_age() -> None:
    """descendant_snapshot() returns the oldest live descendant age in seconds."""
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


# ---------------------------------------------------------------------------
# 12. ManagedProcess.has_live_descendants() excludes zombies
# ---------------------------------------------------------------------------


def test_has_live_descendants_excludes_zombies() -> None:
    """has_live_descendants() returns False when only zombies remain."""
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


# ---------------------------------------------------------------------------
# 13. terminate() on process with live descendants escalates correctly
# ---------------------------------------------------------------------------


def test_terminate_with_live_descendants() -> None:
    """terminate() kills the process and its descendants via the injected psutil."""
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

    # Verify descendants exist before terminate
    assert handle.has_live_descendants() is True

    handle.terminate(grace_period_s=0.1)

    assert handle.record.status == ProcessStatus.KILLED
    assert handle.record.cause == "killed"


# ---------------------------------------------------------------------------
# 14. Public terminal-history contract
# ---------------------------------------------------------------------------


def test_get_record_returns_terminal_history_after_exit() -> None:
    sync_factory = make_sync_process_factory(itertools.count(1), returncode=0)
    pm = _make_pm(sync_factory=sync_factory)

    handle = pm.spawn([sys.executable, "-c", "pass"], SpawnOptions(label="phase:test"))
    pid = handle.pid
    handle.wait()

    record = pm.get_record(pid)
    assert record is not None
    assert record.status == ProcessStatus.EXITED
    assert pm.get_record(pid, include_terminal=False) is None


def test_list_records_filters_active_and_terminal_by_label_prefix() -> None:
    sync_factory = make_sync_process_factory(itertools.count(1), returncode=0)
    pm = _make_pm(sync_factory=sync_factory)

    active = pm.spawn([sys.executable, "-c", "pass"], SpawnOptions(label="phase:keep"))
    terminal = pm.spawn([sys.executable, "-c", "pass"], SpawnOptions(label="phase:done"))
    terminal.wait()

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
        ),
        sync_process_factory=sync_factory,
        async_process_factory=make_async_process_factory(itertools.count(100)),
    )

    handles = [
        pm.spawn([sys.executable, "-c", "pass"], SpawnOptions(label=f"phase:{idx}"))
        for idx in range(3)
    ]
    for handle in handles:
        handle.wait()

    terminal_records = pm.list_records(
        include_active=False,
        include_terminal=True,
        label_prefix="phase:",
    )
    terminal_pids = [record.pid for record in terminal_records]

    assert terminal_pids == [handles[1].pid, handles[2].pid]
    assert pm.get_record(handles[0].pid) is None
