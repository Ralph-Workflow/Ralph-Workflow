"""Tests for wt-16: exit-status harvest from reaped processes.

Verifies that _reap_psutil_zombie_and_mark, _reap_async_in_sync_and_mark,
_escalate_termination_sync, _escalate_with_psutil, _terminate_by_pid,
and _terminate_psutil_wave all populate record.returncode from the
dead-list returncode after _safe_wait_procs / os.waitpid reaps the root.
"""

from __future__ import annotations

import os
import signal
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from ralph.process.manager import ManagedPtyProcess, ProcessManager, ProcessManagerPolicy
from ralph.process.manager._managed_process import ManagedProcess
from ralph.process.manager._process_record import ProcessRecord
from ralph.process.manager._process_status import ProcessStatus
from ralph.testing.fake_process import FakeAsyncProcess, FakePsutil, FakePsutilProcess

if TYPE_CHECKING:
    from ralph.process.manager._process_manager_types import (
        _AsyncProcessLike,
        _PsutilModuleLike,
        _PsutilProcessLike,
        _SyncProcessLike,
    )

pytestmark = pytest.mark.timeout_seconds(5)

_FAST_POLICY = ProcessManagerPolicy(
    default_grace_period_s=0.0,
    kill_followup_timeout_s=0.0,
    log_events=False,
    enable_zombie_reaper=False,
)


class _PsutilProcessWithReturncode(FakePsutilProcess):
    """Fake psutil process with a typed returncode attribute."""

    returncode: int | None = None


def _make_record(manager: ProcessManager, pid: int) -> ProcessRecord:
    """Create and inject a RUNNING ProcessRecord for the given manager."""
    record = ProcessRecord(
        pid=pid,
        pgid=pid,
        command=("test",),
        cwd=None,
        started_at=datetime.now(tz=UTC),
        status=ProcessStatus.RUNNING,
    )
    manager._records[pid] = record
    return record


# ----------------------------------------------------------------------
# Test 1: _reap_psutil_zombie_and_mark decodes psutil returncode directly
# ----------------------------------------------------------------------


def test_psutil_zombie_reap_decodes_psutil_returncode() -> None:
    """When psutil.wait_procs reaps the root and sets returncode on the
    psutil_proc object, _reap_psutil_zombie_and_mark propagates it to the
    record."""
    manager = ProcessManager(policy=_FAST_POLICY)
    record = _make_record(manager, pid=1)

    fake_root = _PsutilProcessWithReturncode(pid=1)
    fake_root._terminated = True  # is_running() -> False -> wait_procs puts it in dead
    fake_root.returncode = -9

    fake_psutil = FakePsutil()
    fake_psutil._processes = {1: fake_root}

    manager._reap_psutil_zombie_and_mark(
        record,
        fake_root,  # type: ignore[arg-type]
        fake_psutil,  # type: ignore[arg-type]
        cause="zombie_after_kill",
    )
    assert record.returncode == -9


# ----------------------------------------------------------------------
# Test 2: _reap_psutil_zombie_and_mark decodes waitpid when psutil has no rc
# ----------------------------------------------------------------------


def test_psutil_zombie_reap_decodes_waitpid_when_no_psutil_returncode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the psutil process has returncode==None but os.waitpid returns
    a WEXITSTATUS-encoded exit code, _reap_psutil_zombie_and_mark decodes it
    via _status_to_returncode."""
    manager = ProcessManager(policy=_FAST_POLICY)
    record = _make_record(manager, pid=1)

    fake_root = _PsutilProcessWithReturncode(pid=1)
    fake_root._terminated = True

    # Encode exit code 7 as the raw kernel wait status: n << 8
    encoded_status = 7 << 8  # 0x700

    def fake_waitpid(pid: int, flags: int) -> tuple[int, int]:
        return (1, encoded_status)

    monkeypatch.setattr(os, "waitpid", fake_waitpid)

    fake_psutil = FakePsutil()
    fake_psutil._processes = {1: fake_root}

    manager._reap_psutil_zombie_and_mark(
        record,
        cast("_PsutilProcessLike", fake_root),
        cast("_PsutilModuleLike", fake_psutil),
        cause="zombie_after_kill",
    )
    assert record.returncode == 7


# ----------------------------------------------------------------------
# Test 3: _reap_async_in_sync_and_mark decodes waitpid status
# ----------------------------------------------------------------------


def test_reap_async_in_sync_decodes_waitpid_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When os.waitpid reaps an async process and returns a signal status
    (SIGTERM=15), _reap_async_in_sync_and_mark decodes it correctly."""
    manager = ProcessManager(policy=_FAST_POLICY)
    record = _make_record(manager, pid=42)

    fake_async = FakeAsyncProcess(pid=42)

    # Encode SIGTERM as the raw kernel wait status: just the signal number
    encoded_signal_status = signal.SIGTERM

    def fake_waitpid(pid: int, flags: int) -> tuple[int, int]:
        return (42, encoded_signal_status)

    monkeypatch.setattr(os, "waitpid", fake_waitpid)

    manager._reap_async_in_sync_and_mark(
        record, cast("_AsyncProcessLike", fake_async), cause="zombie_after_kill"
    )
    assert record.returncode == -15


# ----------------------------------------------------------------------
# Test 4: _terminate_by_pid harvests gone-list returncode
# ----------------------------------------------------------------------


def test_terminate_by_pid_harvests_gone_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When _safe_wait_procs returns a dead list containing the root with
    returncode==-15, _terminate_by_pid passes that to _mark_killed."""
    manager = ProcessManager(policy=_FAST_POLICY, psutil=cast("_PsutilModuleLike", FakePsutil()))
    record = _make_record(manager, pid=7)

    fake_root = _PsutilProcessWithReturncode(pid=7)
    fake_root._terminated = True
    fake_root.returncode = -15

    fake_psutil = FakePsutil()
    fake_psutil._processes = {7: fake_root}

    def fake_wait_procs_with_dead(
        procs: Sequence[object], timeout: float | None = None
    ) -> tuple[list[_PsutilProcessLike], list[_PsutilProcessLike]]:
        return [cast("_PsutilProcessLike", fake_root)], []

    monkeypatch.setattr(fake_psutil, "wait_procs", fake_wait_procs_with_dead)
    manager._psutil = cast("_PsutilModuleLike", fake_psutil)

    manager._terminate_by_pid(record, grace_period_s=0.01)
    assert record.returncode == -15


# ----------------------------------------------------------------------
# Test 5: ManagedPtyProcess.returncode falls back to record
# ----------------------------------------------------------------------


class _StubPtyProcForManaged:
    """Stub satisfying _PtyProcessLike protocol with no returncode."""

    pid = 99998
    master_fd = -1
    slave_fd = -1

    @property
    def returncode(self) -> int | None:
        return None

    def poll(self) -> int | None:
        return None

    def wait(self, timeout: float | None = None) -> int:
        return 0

    def terminate(self) -> None:
        pass

    def kill(self) -> None:
        pass

    def close(self) -> None:
        pass

    def fileno(self) -> int:
        return -1

    def isatty(self) -> bool:
        return False

    def note_external_returncode(self, rc: int) -> None:
        pass


def test_managed_pty_returncode_falls_back_to_record() -> None:
    """When _proc.returncode is None but the record is terminal with an int
    returncode, ManagedPtyProcess.returncode falls back to the record."""
    record = ProcessRecord(
        pid=99998,
        pgid=99998,
        command=("test",),
        cwd=None,
        started_at=datetime.now(tz=UTC),
        status=ProcessStatus.KILLED,
        ended_at=datetime.now(tz=UTC),
        cause="killed",
        returncode=-15,
    )
    manager = ProcessManager(policy=_FAST_POLICY)
    handle = ManagedPtyProcess(_StubPtyProcForManaged(), record, manager)
    assert handle.returncode == -15


# ----------------------------------------------------------------------
# Test 6: _escalate_termination_sync harvests psutil returncode
# ----------------------------------------------------------------------


class _FakeSyncProc:
    pid = 5
    stdin = None
    stdout = None
    stderr = None

    def __init__(self, callback: list[int] | None = None) -> None:
        self._callback = callback

    @property
    def returncode(self) -> int | None:
        return None

    def poll(self) -> int | None:
        return None

    def wait(self, timeout: float | None = None) -> int:
        return 0

    def communicate(
        self,
        input: bytes | None = None,
        timeout: float | None = None,
    ) -> tuple[bytes | None, bytes | None]:
        return None, None

    def terminate(self) -> None:
        pass

    def kill(self) -> None:
        pass

    def note_external_returncode(self, rc: int) -> None:
        if self._callback is not None:
            self._callback.append(rc)


def test_escalate_termination_sync_harvests_psutil_returncode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When _safe_wait_procs returns the root in its dead list with
    returncode==-15, _escalate_termination_sync passes it to _mark_killed
    and calls note_external_returncode on the proc."""
    manager = ProcessManager(policy=_FAST_POLICY)
    record = _make_record(manager, pid=5)
    manager.record_terminal_reason(record.pid, "interactive_completion", "test")

    fake_root = _PsutilProcessWithReturncode(pid=5)
    fake_root._terminated = True
    fake_root.returncode = -15

    fake_psutil = FakePsutil()
    fake_psutil._processes = {5: fake_root}
    manager._psutil = cast("_PsutilModuleLike", fake_psutil)

    note_called_with: list[int] = []
    fake_proc = _FakeSyncProc(note_called_with)

    def fake_wait_procs_with_dead(
        procs: Sequence[object], timeout: float | None = None
    ) -> tuple[list[_PsutilProcessLike], list[_PsutilProcessLike]]:
        return [cast("_PsutilProcessLike", fake_root)], []

    monkeypatch.setattr(fake_psutil, "wait_procs", fake_wait_procs_with_dead)

    manager._escalate_termination_sync(
        record, cast("_SyncProcessLike", fake_proc), grace_period_s=0.01
    )
    assert record.returncode == -15
    assert -15 in note_called_with


# ----------------------------------------------------------------------
# Test 7: _terminate_psutil_wave harvests dead_rc into records
# ----------------------------------------------------------------------


def test_terminate_psutil_wave_harvests_dead_rc(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When _terminate_psutil_wave's wait_procs returns a dead list entry
    whose pid matches a tracked record, it calls _mark_killed on that record."""
    manager = ProcessManager(policy=_FAST_POLICY)
    record = _make_record(manager, pid=11)

    fake_root = _PsutilProcessWithReturncode(pid=11)
    fake_root._terminated = True
    fake_root.returncode = -9

    fake_psutil = FakePsutil()
    fake_psutil._processes = {11: fake_root}

    managed_proc = ManagedProcess.__new__(ManagedProcess)
    managed_proc._proc = cast("_SyncProcessLike", _FakeSyncProc())
    managed_proc._record = record
    managed_proc._manager = manager

    def fake_wait_procs_with_dead(
        procs: Sequence[object], timeout: float | None = None
    ) -> tuple[list[_PsutilProcessLike], list[_PsutilProcessLike]]:
        return [cast("_PsutilProcessLike", fake_root)], []

    monkeypatch.setattr(fake_psutil, "wait_procs", fake_wait_procs_with_dead)

    survivors = managed_proc._terminate_psutil_wave(
        cast("_PsutilModuleLike", fake_psutil),
        [cast("_PsutilProcessLike", fake_root)],
        grace_period_s=0.0,
    )
    assert record.returncode == -9
    assert len(survivors) == 0


# ----------------------------------------------------------------------
# Test 8: _kill_psutil_wave harvests dead_rc into records
# ----------------------------------------------------------------------


def test_kill_psutil_wave_harvests_dead_rc(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When _kill_psutil_wave's wait_procs returns a dead list entry
    whose pid matches a tracked record, it sets returncode on that record."""
    manager = ProcessManager(policy=_FAST_POLICY)
    record = _make_record(manager, pid=12)

    fake_root = _PsutilProcessWithReturncode(pid=12)
    fake_root._terminated = True
    fake_root.returncode = -15

    fake_psutil = FakePsutil()
    fake_psutil._processes = {12: fake_root}

    managed_proc = ManagedProcess.__new__(ManagedProcess)
    managed_proc._proc = cast("_SyncProcessLike", _FakeSyncProc())
    managed_proc._record = record
    managed_proc._manager = manager

    def fake_wait_procs_with_dead(
        procs: Sequence[object], timeout: float | None = None
    ) -> tuple[list[_PsutilProcessLike], list[_PsutilProcessLike]]:
        return [cast("_PsutilProcessLike", fake_root)], []

    monkeypatch.setattr(fake_psutil, "wait_procs", fake_wait_procs_with_dead)

    survivors = managed_proc._kill_psutil_wave(
        cast("_PsutilModuleLike", fake_psutil),
        [cast("_PsutilProcessLike", fake_root)],
    )
    assert record.returncode == -15
    assert len(survivors) == 0


# ----------------------------------------------------------------------
# Test 9: original_terminal_reason returns first recorded reason
# ----------------------------------------------------------------------


def test_original_terminal_reason_returns_first_recorded() -> None:
    """ProcessManager.original_terminal_reason returns the earliest recorded reason."""
    manager = ProcessManager(policy=_FAST_POLICY)
    pid = 99999
    manager.record_terminal_reason(pid, "interactive_completion", "agy")
    manager._record_termination_outcome(pid, "graceful_terminate", "sent")
    manager._record_termination_outcome(pid, "force_kill", "sent")
    assert manager.original_terminal_reason(pid) == "interactive_completion"
    assert manager.termination_issuer(pid) == "sent"
