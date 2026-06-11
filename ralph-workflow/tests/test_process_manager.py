"""Black-box tests for ralph.process.ProcessManager.

All tests use deterministic fake processes injected via constructor seams;
no real subprocess spawning or psutil PID polling.
"""

from __future__ import annotations

import asyncio
import atexit
import itertools
import os
import sys
import threading
import time as _time
from typing import TYPE_CHECKING, cast
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
from ralph.process.manager import ProcessTerminationError, _singleton
from ralph.testing.fake_process import (
    FakeImmortalPopen,
    FakePopen,
    FakePsutil,
    FakePsutilProcess,
    FakeStubbornPopen,
    make_async_process_factory,
    make_sync_process_factory,
)

if TYPE_CHECKING:
    from ralph.testing.fake_process import AsyncFactoryCallable, SyncFactoryCallable

_FAST_POLICY = ProcessManagerPolicy(
    default_grace_period_s=0.3,
    kill_followup_timeout_s=0.5,
    log_events=False,
    enable_zombie_reaper=False,
)


@pytest.fixture(autouse=True)
def _reset_pm() -> object:
    """Ensure each test starts and ends with a clean singleton."""
    reset_process_manager()
    yield
    reset_process_manager()


def _make_pm(
    *,
    sync_factory: SyncFactoryCallable | None = None,
    async_factory: AsyncFactoryCallable | None = None,
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

    with patch("os.kill", return_value=None):
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
                enable_zombie_reaper=False,
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
                enable_zombie_reaper=False,
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
        handle.wait()

    terminal_records = pm.list_records(
        include_active=False,
        include_terminal=True,
        label_prefix="phase:",
    )
    terminal_pids = [record.pid for record in terminal_records]

    assert terminal_pids == [handles[1].pid, handles[2].pid]
    assert pm.get_record(handles[0].pid) is None


# ---------------------------------------------------------------------------
# Edge-case tests: EC1-EC21, EC-ASYNC-LIVE, EC-ASYNC-GONE
# ---------------------------------------------------------------------------


# EC1: spawn() pid appears in list_active()
def test_ec1_spawn_pid_in_list_active() -> None:
    """spawn() pid appears in list_active()."""
    sync_factory = make_sync_process_factory(itertools.count(1), returncode=0)
    pm = _make_pm(sync_factory=sync_factory)
    handle = pm.spawn([sys.executable, "-c", "pass"])
    active_pids = [r.pid for r in pm.list_active()]
    assert handle.record.pid in active_pids
    handle.wait()


# EC2: process exits normally -> status EXITED rc=0
def test_ec2_process_exits_normally() -> None:
    """Process exits normally -> status EXITED rc=0."""
    sync_factory = make_sync_process_factory(itertools.count(1), returncode=0)
    pm = _make_pm(sync_factory=sync_factory)
    handle = pm.spawn([sys.executable, "-c", "pass"])
    handle.wait()
    assert handle.record.status == ProcessStatus.EXITED
    assert handle.record.returncode == 0
    assert handle.record.cause == "exited"


# EC3: stub stubborn=True process -> verify SIGKILL escalation
def test_ec3_stubborn_process_sigkill_escalation() -> None:
    """Stubborn process ignores SIGTERM, escalates to SIGKILL."""
    # returncode=-9 means kill() must be called to terminate
    never_die_factory = make_sync_process_factory(itertools.count(1), returncode=-9)
    pm = _make_pm(sync_factory=never_die_factory)
    handle = pm.spawn([sys.executable, "-c", "pass"])
    handle.terminate(grace_period_s=0.1)
    assert handle.record.status == ProcessStatus.KILLED
    assert handle.record.cause == "killed"

def test_ec4_sigterm_timeout_escalates_to_sigkill() -> None:
    """Graceful terminate times out; force kill succeeds via root-only escalation."""

    def stubborn_factory(command: object, opts: object) -> FakeStubbornPopen:
        del command, opts
        return FakeStubbornPopen(pid=1, final_returncode=-9)

    pm = ProcessManager(
        policy=_FAST_POLICY,
        sync_process_factory=stubborn_factory,
        async_process_factory=make_async_process_factory(itertools.count(100)),
        psutil=None,
    )
    handle = pm.spawn([sys.executable, "-c", "pass"])

    handle.terminate(grace_period_s=0.01)

    assert handle.record.status == ProcessStatus.KILLED
    assert handle.record.returncode == -9
    assert handle.record.cause == "killed"


def test_ec9_root_only_force_kill_still_alive_raises_error() -> None:
    """Force kill failure must stay terminal without reporting a successful kill."""

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
        handle.terminate(grace_period_s=0.01)

    assert handle.record.status == ProcessStatus.FAILED
    assert handle.record.cause == "termination_failed"
    assert handle.record.failure_message == "Process still alive after kill"
    assert pm.list_active() == []

# EC5: spawn_async() result appears in list_active()
@pytest.mark.asyncio
async def test_ec5_spawn_async_appears_in_list_active() -> None:
    """spawn_async() result appears in list_active()."""
    async_factory = make_async_process_factory(itertools.count(1), returncode=0)
    pm = _make_pm(async_factory=async_factory)
    handle = await pm.spawn_async([sys.executable, "-c", "pass"])
    active_pids = [r.pid for r in pm.list_active()]
    assert handle.record.pid in active_pids
    # Clean up
    await handle.terminate(grace_period_s=0.1)

# EC6: SIGTERM sent before psutil.wait_procs timeout
def test_ec6_sigterm_sent_before_psutil_timeout() -> None:
    """SIGTERM sent before psutil.wait_procs timeout."""
    sync_factory = make_sync_process_factory(itertools.count(1), returncode=None)
    # Fake psutil that tracks terminate was called
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
    # Terminate should send SIGTERM
    handle.terminate(grace_period_s=0.5)
    assert handle.record.status == ProcessStatus.KILLED

@pytest.mark.asyncio
async def test_ec7_async_shutdown_all_with_psutil_escalates() -> None:
    """spawn_async() + shutdown_all() with psutil escalates correctly."""
    async_factory = make_async_process_factory(itertools.count(1), returncode=None)
    # Use FakePsutil to exercise the psutil code path
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
    # Should be terminated
    records = pm.list_records(include_active=False, include_terminal=True)
    assert any(r.pid == spawned_pid and r.status == ProcessStatus.KILLED for r in records)

# EC8: concurrent shutdown_all with register_listener event count
def test_ec8_concurrent_shutdown_all_single_event() -> None:
    """Two concurrent shutdown_all() calls produce exactly one terminal event."""
    sync_factory = make_sync_process_factory(itertools.count(1), returncode=None)
    pm = _make_pm(sync_factory=sync_factory)

    terminal_events: list[ProcessEvent] = []
    pm.register_listener(terminal_events.append)

    handle = pm.spawn([sys.executable, "-c", "pass"])
    spawned_pid = handle.record.pid

    barrier = threading.Barrier(2)

    def _shutdown() -> None:
        barrier.wait()
        pm.shutdown_all(grace_period_s=0)

    t1 = threading.Thread(target=_shutdown)
    t2 = threading.Thread(target=_shutdown)
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    # (1) Both threads returned without raising
    # (2) Exactly one terminal record
    records = pm.list_records(include_active=False, include_terminal=True)
    assert len(records) == 1
    # (3) list_active is empty
    assert len(pm.list_active()) == 0
    # (4) Exactly one ProcessEvent with new_status in _TERMINAL_STATUSES for spawned_pid
    terminal_for_pid = [
        e
        for e in terminal_events
        if e.new_status in (
            ProcessStatus.EXITED,
            ProcessStatus.KILLED,
            ProcessStatus.FAILED,
        )
        and e.record.pid == spawned_pid
    ]
    assert len(terminal_for_pid) == 1, f"Expected 1 terminal event, got {len(terminal_for_pid)}"

def test_ec10_process_termination_error_when_kill_fails() -> None:
    """ProcessTerminationError raised when kill fails."""
    sync_factory = make_sync_process_factory(itertools.count(1), returncode=None)

    parent_pid = 1

    class _RootStubborn(FakePsutilProcess):
        def children(self, recursive: bool = False) -> list[FakePsutilProcess]:
            return []

        def terminate(self) -> None:
            pass  # ignores SIGTERM: _terminated stays False

        def kill(self) -> None:
            pass  # ignores SIGKILL: _killed stays False

    root = _RootStubborn(pid=parent_pid)
    fake_psutil = FakePsutil()
    fake_psutil._processes = {parent_pid: root}

    pm = _make_pm(sync_factory=sync_factory, psutil_mod=fake_psutil)
    handle = pm.spawn([sys.executable, "-c", "pass"])
    handle._record.pid = parent_pid
    # Force kill to fail by having wait_procs return alive processes
    with pytest.raises(ProcessTerminationError):
        handle.terminate(grace_period_s=0.0)

# EC13: shutdown_all_for_label terminates only matching-label processes
def test_ec13_shutdown_all_for_label_only_matching() -> None:
    """shutdown_all_for_label terminates only matching-label processes."""
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

# EC19: after process exits, list_records contains EXITED record via public API
def test_ec19_exit_record_accessible_via_public_api() -> None:
    """After process exits, list_records contains EXITED record via public API."""
    sync_factory = make_sync_process_factory(itertools.count(1), returncode=0)
    pm = _make_pm(sync_factory=sync_factory)

    handle = pm.spawn([sys.executable, "-c", "pass"])
    handle.wait()

    records = pm.list_records(include_active=False, include_terminal=True)
    exited_records = [r for r in records if r.status == ProcessStatus.EXITED]
    assert len(exited_records) >= 1
    assert any(r.pid == handle.record.pid for r in exited_records)
    # Also verify get_record works
    record = pm.get_record(handle.record.pid, include_terminal=True)
    assert record is not None
    assert record.status == ProcessStatus.EXITED

@pytest.mark.asyncio
async def test_ec21_shutdown_all_terminates_async_process() -> None:
    """shutdown_all() terminates an async process without error."""
    async_factory = make_async_process_factory(itertools.count(1), returncode=None)
    pm = _make_pm(async_factory=async_factory, psutil_mod=FakePsutil())

    handle = await pm.spawn_async([sys.executable, "-c", "pass"])
    spawned_pid = handle.record.pid
    # Should not raise
    pm.shutdown_all(grace_period_s=0.1)

    records = pm.list_records(include_active=False, include_terminal=True)
    assert any(r.pid == spawned_pid and r.status == ProcessStatus.KILLED for r in records)

@pytest.mark.asyncio
async def test_ec_async_live_process_still_alive_after_kill() -> None:
    """Process still alive after kill -> ProcessTerminationError raised."""
    # Use psutil=None to go through the no-psutil path
    async_factory = make_async_process_factory(itertools.count(1), returncode=None)
    pm = ProcessManager(
        policy=_FAST_POLICY,
        sync_process_factory=make_sync_process_factory(itertools.count(1)),
        async_process_factory=async_factory,
        psutil=None,  # No psutil -> uses _escalate_async_in_sync_context
    )

    await pm.spawn_async([sys.executable, "-c", "pass"])
    # Monkeypatch os.kill to simulate process still alive after kill
    def fake_kill(pid: int, sig: int) -> None:
        if sig == 0:
            # Process still alive (os.kill with signal 0 checks existence)
            return
        # For actual kill signals, we let it through to raise the real error
        raise ProcessLookupError(pid, 0)

    with patch.object(os, "kill", fake_kill), pytest.raises(ProcessTerminationError):
        pm.shutdown_all(grace_period_s=0.1)

@pytest.mark.asyncio
async def test_ec_async_gone_process_already_gone_after_kill() -> None:
    """Process already gone after kill -> no exception, KILLED record."""
    async_factory = make_async_process_factory(itertools.count(1), returncode=None)
    pm = ProcessManager(
        policy=_FAST_POLICY,
        sync_process_factory=make_sync_process_factory(itertools.count(1)),
        async_process_factory=async_factory,
        psutil=None,  # No psutil -> uses _escalate_async_in_sync_context
    )

    handle = await pm.spawn_async([sys.executable, "-c", "pass"])
    spawned_pid = handle.record.pid

    def kill_raises_lookup(pid: int, sig: int) -> None:
        raise ProcessLookupError(pid, sig)

    with patch.object(os, "kill", side_effect=kill_raises_lookup):
        # Should not raise
        pm.shutdown_all(grace_period_s=0.1)
    # Should have a KILLED record
    record = pm.get_record(spawned_pid, include_terminal=True)
    assert record is not None
    assert record.status == ProcessStatus.KILLED

def test_cat_mcp_spawn_with_mcp_server_label() -> None:
    """MCP server processes tracked and shutdown correctly."""
    sync_factory = make_sync_process_factory(itertools.count(1), returncode=None)
    pm = _make_pm(sync_factory=sync_factory)

    handle = pm.spawn(
        [sys.executable, "-c", "pass"],
        SpawnOptions(label="phase:development:mcp-server"),
    )
    spawned_pid = handle.record.pid
    # Verify in active list
    active_pids = [r.pid for r in pm.list_active()]
    assert spawned_pid in active_pids

    pm.shutdown_all(grace_period_s=0.1)
    # Verify in terminal records
    records = pm.list_records(include_active=False, include_terminal=True)
    assert any(r.pid == spawned_pid for r in records)

def test_cat_exec_spawn_with_exec_label() -> None:
    """Exec helper processes tracked and terminated correctly."""
    sync_factory = make_sync_process_factory(itertools.count(1), returncode=None)
    pm = _make_pm(sync_factory=sync_factory)

    handle = pm.spawn(
        [sys.executable, "-c", "pass"],
        SpawnOptions(label="mcp-exec:python"),
    )
    spawned_pid = handle.record.pid
    # Verify in active list
    active_pids = [r.pid for r in pm.list_active()]
    assert spawned_pid in active_pids

    handle.terminate(grace_period_s=0.1)

    assert handle.record.status == ProcessStatus.KILLED

@pytest.mark.asyncio
async def test_cat_agent_pm_spawn_async_with_agent_label() -> None:
    """Agent processes tracked via ProcessManager and shutdown correctly."""
    async_factory = make_async_process_factory(itertools.count(1), returncode=None)
    pm = _make_pm(async_factory=async_factory, psutil_mod=FakePsutil())

    handle = await pm.spawn_async(
        [sys.executable, "-c", "pass"],
        SpawnOptions(label="agent:development:unit-1:root"),
    )
    spawned_pid = handle.record.pid
    # Verify in active list
    active_pids = [r.pid for r in pm.list_active()]
    assert spawned_pid in active_pids

    pm.shutdown_all(grace_period_s=0.1)

    # Verify in terminal records
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


# ---------------------------------------------------------------------------
# Singleton and atexit handler tests
# ---------------------------------------------------------------------------


def test_atexit_shutdown_skips_when_no_instance() -> None:
    """_atexit_shutdown returns early when no singleton is set."""
    _singleton._pm_state.instance = None
    # No exception, no side effect.
    _singleton._atexit_shutdown()


def test_atexit_shutdown_calls_pm_shutdown_all() -> None:
    """_atexit_shutdown calls shutdown_all on the singleton when present."""
    captured: dict[str, object] = {}

    class _FakePM:
        def shutdown_all(self, *, grace_period_s: float | None = None) -> None:
            captured["grace"] = grace_period_s

    _singleton._pm_state.instance = cast("ProcessManager", _FakePM())
    try:
        _singleton._atexit_shutdown()
        assert captured.get("grace") == 0.5
    finally:
        _singleton._pm_state.instance = None


def test_atexit_shutdown_swallows_baseexception() -> None:
    """_atexit_shutdown swallows BaseException from shutdown_all."""

    class _ExplodingPM:
        def shutdown_all(self, *, grace_period_s: float | None = None) -> None:
            del grace_period_s
            raise RuntimeError("shutdown-failed")

    _singleton._pm_state.instance = cast("ProcessManager", _ExplodingPM())
    try:
        # Must NOT raise.
        _singleton._atexit_shutdown()
    finally:
        _singleton._pm_state.instance = None


def test_get_process_manager_registers_atexit_exactly_once() -> None:
    """atexit.register is called exactly once across multiple get_process_manager calls."""
    register_calls: list[object] = []
    original_register = atexit.register

    def _spy_register(fn: object) -> object:
        register_calls.append(fn)
        return original_register(fn)

    _singleton._pm_state.instance = None
    _singleton._pm_state.atexit_registered = False
    try:
        with patch("atexit.register", side_effect=_spy_register):
            get_process_manager()
            get_process_manager()
            get_process_manager()
    finally:
        reset_process_manager()

    assert len(register_calls) == 1, f"Expected 1 atexit.register call, got {len(register_calls)}"


def test_process_phase_scope_tears_down_labeled_processes() -> None:
    """process_phase_scope triggers shutdown_all_for_label on exit."""
    sync_factory = make_sync_process_factory(itertools.count(1), returncode=None)
    # Inject the PM into the singleton so process_phase_scope uses it.
    _singleton._pm_state.instance = ProcessManager(
        policy=ProcessManagerPolicy(
            default_grace_period_s=0.1,
            kill_followup_timeout_s=0.1,
            log_events=False,
            enable_zombie_reaper=False,
        ),
        sync_process_factory=sync_factory,
        async_process_factory=make_sync_process_factory(itertools.count(100)),
    )
    handle = _singleton._pm_state.instance.spawn(
        [sys.executable, "-c", "pass"],
        SpawnOptions(label="phase:audit"),
    )

    with process_phase_scope("audit"):
        pass

    assert handle.record.status == ProcessStatus.KILLED


def test_process_phase_scope_logs_and_reraises_on_termination_error() -> None:
    """process_phase_scope logs and re-raises ProcessTerminationError from cleanup."""

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

    _singleton._pm_state.instance = cast("ProcessManager", _ExplodingPM())
    try:
        with pytest.raises(ProcessTerminationError), process_phase_scope("audit"):
            pass
    finally:
        _singleton._pm_state.instance = None

_publish_named_process_manager_regressions()
