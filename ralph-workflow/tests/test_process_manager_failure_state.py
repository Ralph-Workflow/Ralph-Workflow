"""Regression tests for truthful termination-failure bookkeeping."""

from __future__ import annotations

import itertools
import os
import sys
from unittest.mock import patch

import pytest

from ralph.process.manager import ProcessManager, ProcessManagerPolicy, ProcessTerminationError
from ralph.process.manager._process_status import ProcessStatus
from ralph.testing.fake_process import (
    FakeImmortalPopen,
    FakePsutil,
    FakePsutilProcess,
    make_async_process_factory,
    make_sync_process_factory,
)

_FAST_POLICY = ProcessManagerPolicy(
    default_grace_period_s=0.3,
    kill_followup_timeout_s=0.5,
    log_events=False,
)


class _FakeAccessDeniedError(Exception):
    """Synthetic access-denied error for process-manager shutdown tests."""


class _FakeNoSuchProcessError(Exception):
    """Synthetic no-such-process error for process-manager shutdown tests."""


class _AccessDeniedPsutil(FakePsutil):
    """Fake psutil adapter that denies access for configured PIDs."""

    NoSuchProcess = _FakeNoSuchProcessError
    AccessDenied = _FakeAccessDeniedError

    def __init__(self, denied_pids: set[int]) -> None:
        super().__init__()
        self._denied_pids = denied_pids

    def process_from_pid(self, pid: int) -> FakePsutilProcess:
        if pid in self._denied_pids:
            raise self.AccessDenied(pid)
        return super().process_from_pid(pid)


def test_root_only_force_kill_failure_marks_record_failed() -> None:
    """No-psutil termination failure must not be recorded as KILLED."""

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


def test_psutil_force_kill_failure_marks_record_failed() -> None:
    """psutil-based force-kill failure must leave a FAILED terminal record."""
    sync_factory = make_sync_process_factory(itertools.count(1), returncode=None)

    class _RootStubborn(FakePsutilProcess):
        def children(self, recursive: bool = False) -> list[FakePsutilProcess]:
            return []

        def terminate(self) -> None:
            pass

        def kill(self) -> None:
            pass

    parent_pid = 1
    fake_psutil = FakePsutil()
    fake_psutil._processes = {parent_pid: _RootStubborn(pid=parent_pid)}

    pm = ProcessManager(
        policy=_FAST_POLICY,
        sync_process_factory=sync_factory,
        async_process_factory=make_async_process_factory(itertools.count(100)),
        psutil=fake_psutil,
    )
    handle = pm.spawn([sys.executable, "-c", "pass"])
    handle._record.pid = parent_pid

    with pytest.raises(ProcessTerminationError):
        handle.terminate(grace_period_s=0.0)

    assert handle.record.status == ProcessStatus.FAILED
    assert handle.record.cause == "termination_failed"
    assert handle.record.failure_message == "Process still alive after kill"
    assert pm.list_active() == []


@pytest.mark.asyncio
async def test_async_shutdown_failure_marks_record_failed() -> None:
    """Async shutdown in sync context must surface failure and preserve truthful state."""
    async_factory = make_async_process_factory(itertools.count(1), returncode=None)
    pm = ProcessManager(
        policy=_FAST_POLICY,
        sync_process_factory=make_sync_process_factory(itertools.count(100)),
        async_process_factory=async_factory,
        psutil=None,
    )
    handle = await pm.spawn_async([sys.executable, "-c", "pass"])
    spawned_pid = handle.record.pid

    def fake_kill(pid: int, sig: int) -> None:
        if sig == 0:
            return
        raise ProcessLookupError(pid, 0)

    with patch.object(os, "kill", fake_kill), pytest.raises(ProcessTerminationError):
        pm.shutdown_all(grace_period_s=0.1)

    record = pm.get_record(spawned_pid, include_terminal=True)
    assert record is not None
    assert record.status == ProcessStatus.FAILED
    assert record.cause == "termination_failed"
    assert record.failure_message == "Process still alive after kill"
    assert pm.list_active() == []


def test_terminate_by_pid_access_denied_marks_record_failed() -> None:
    """Stale tracked records must surface access-denied cleanup failures."""
    sync_factory = make_sync_process_factory(itertools.count(1), returncode=None)
    pm = ProcessManager(
        policy=_FAST_POLICY,
        sync_process_factory=sync_factory,
        async_process_factory=make_async_process_factory(itertools.count(100)),
        psutil=_AccessDeniedPsutil({1}),
    )
    handle = pm.spawn([sys.executable, "-c", "pass"])
    pm._sync_procs.pop(handle.pid)

    with pytest.raises(ProcessTerminationError):
        pm.shutdown_all(grace_period_s=0.01)

    assert handle.record.status == ProcessStatus.FAILED
    assert handle.record.cause == "termination_failed"
    assert handle.record.failure_message == "Access denied while terminating process"
    assert pm.list_active() == []


def test_escalate_termination_sync_access_denied_marks_record_failed() -> None:
    """Sync handle termination must surface access-denied cleanup failures."""
    sync_factory = make_sync_process_factory(itertools.count(1), returncode=None)
    pm = ProcessManager(
        policy=_FAST_POLICY,
        sync_process_factory=sync_factory,
        async_process_factory=make_async_process_factory(itertools.count(100)),
        psutil=_AccessDeniedPsutil({1}),
    )
    handle = pm.spawn([sys.executable, "-c", "pass"])

    with pytest.raises(ProcessTerminationError):
        handle.terminate(grace_period_s=0.01)

    assert handle.record.status == ProcessStatus.FAILED
    assert handle.record.cause == "termination_failed"
    assert handle.record.failure_message == "Access denied while terminating process"
    assert pm.list_active() == []


@pytest.mark.asyncio
async def test_escalate_async_in_sync_context_access_denied_marks_record_failed() -> None:
    """shutdown_all() for async records must surface access-denied cleanup failures."""
    async_factory = make_async_process_factory(itertools.count(1), returncode=None)
    pm = ProcessManager(
        policy=_FAST_POLICY,
        sync_process_factory=make_sync_process_factory(itertools.count(100)),
        async_process_factory=async_factory,
        psutil=_AccessDeniedPsutil({1}),
    )
    handle = await pm.spawn_async([sys.executable, "-c", "pass"])

    with pytest.raises(ProcessTerminationError):
        pm.shutdown_all(grace_period_s=0.01)

    assert handle.record.status == ProcessStatus.FAILED
    assert handle.record.cause == "termination_failed"
    assert handle.record.failure_message == "Access denied while terminating process"
    assert pm.list_active() == []


@pytest.mark.asyncio
async def test_escalate_termination_async_access_denied_marks_record_failed() -> None:
    """Async handle termination must also surface access-denied cleanup failures."""
    async_factory = make_async_process_factory(itertools.count(1), returncode=None)
    pm = ProcessManager(
        policy=_FAST_POLICY,
        sync_process_factory=make_sync_process_factory(itertools.count(100)),
        async_process_factory=async_factory,
        psutil=_AccessDeniedPsutil({1}),
    )
    handle = await pm.spawn_async([sys.executable, "-c", "pass"])

    with pytest.raises(ProcessTerminationError):
        await handle.terminate(grace_period_s=0.01)

    assert handle.record.status == ProcessStatus.FAILED
    assert handle.record.cause == "termination_failed"
    assert handle.record.failure_message == "Access denied while terminating process"
    assert pm.list_active() == []


def test_process_phase_scope_raises_on_termination_error() -> None:
    """process_phase_scope must not report a clean exit when cleanup fails."""
    from ralph.process import get_process_manager, process_phase_scope

    def _raise_termination_error(label_prefix: str, *, grace_period_s: float | None = None) -> None:
        raise ProcessTerminationError(pid=99999, pgid=99999)

    pm = get_process_manager()

    with (
        patch.object(pm, "shutdown_all_for_label", _raise_termination_error),
        pytest.raises(ProcessTerminationError),
        process_phase_scope("test-phase"),
    ):
        pass
