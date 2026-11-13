from __future__ import annotations

import asyncio
import os
import signal
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import IO

import pytest

from ralph.process.manager import (
    ManagedProcess,
    ManagedPtyProcess,
    ProcessManager,
    ProcessManagerPolicy,
    ProcessRecord,
    ProcessStatus,
)
from ralph.testing.fake_process import FakeAsyncProcess, FakePsutil, FakePsutilProcess

pytestmark = pytest.mark.timeout_seconds(5)

_FAST_POLICY = ProcessManagerPolicy(
    default_grace_period_s=0.0,
    kill_followup_timeout_s=0.0,
    log_events=False,
    enable_zombie_reaper=False,
)


class _PsutilProcessWithReturncode(FakePsutilProcess):
    returncode: int | None = None


def _make_record(manager: ProcessManager, pid: int) -> ProcessRecord:
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


def test_psutil_zombie_reap_decodes_psutil_returncode() -> None:
    manager = ProcessManager(policy=_FAST_POLICY)
    record = _make_record(manager, pid=1)

    fake_root = _PsutilProcessWithReturncode(pid=1)
    fake_root._terminated = True
    fake_root.returncode = -9

    fake_psutil = FakePsutil()
    fake_psutil._processes = {1: fake_root}

    manager._reap_psutil_zombie_and_mark(
        record,
        fake_root,
        fake_psutil,
        cause="zombie_after_kill",
    )
    assert record.returncode == -9


def test_psutil_zombie_reap_decodes_waitpid_when_no_psutil_returncode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = ProcessManager(policy=_FAST_POLICY)
    record = _make_record(manager, pid=1)

    fake_root = _PsutilProcessWithReturncode(pid=1)
    fake_root._terminated = True

    encoded_status = 7 << 8

    def fake_waitpid(pid: int, flags: int) -> tuple[int, int]:
        return (1, encoded_status)

    monkeypatch.setattr(os, "waitpid", fake_waitpid)

    fake_psutil = FakePsutil()
    fake_psutil._processes = {1: fake_root}

    manager._reap_psutil_zombie_and_mark(
        record,
        fake_root,
        fake_psutil,
        cause="zombie_after_kill",
    )
    assert record.returncode == 7


def test_reap_async_in_sync_decodes_waitpid_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = ProcessManager(policy=_FAST_POLICY)
    record = _make_record(manager, pid=42)

    fake_async = FakeAsyncProcess(pid=42)

    encoded_signal_status = signal.SIGTERM

    def fake_waitpid(pid: int, flags: int) -> tuple[int, int]:
        return (42, encoded_signal_status)

    monkeypatch.setattr(os, "waitpid", fake_waitpid)

    manager._reap_async_in_sync_and_mark(record, fake_async, cause="zombie_after_kill")
    assert record.returncode == -15


def test_terminate_by_pid_harvests_gone_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = ProcessManager(policy=_FAST_POLICY, psutil=FakePsutil())
    record = _make_record(manager, pid=7)

    fake_root = _PsutilProcessWithReturncode(pid=7)
    fake_root._terminated = True
    fake_root.returncode = -15

    fake_psutil = FakePsutil()
    fake_psutil._processes = {7: fake_root}

    def fake_wait_procs_with_dead(
        procs: Sequence[object], timeout: float | None = None
    ) -> tuple[list[FakePsutilProcess], list[FakePsutilProcess]]:
        return [fake_root], []

    monkeypatch.setattr(fake_psutil, "wait_procs", fake_wait_procs_with_dead)
    manager._psutil = fake_psutil

    manager._terminate_by_pid(record, grace_period_s=0.01)
    assert record.returncode == -15


class _StubPtyProcForManaged:
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

    def terminate(self, **_kwargs: object) -> None:
        pass

    def kill(self, **_kwargs: object) -> None:
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


class _FakeSyncProc:
    pid: int = 5
    stdin: IO[bytes] | None = None
    stdout: IO[bytes] | None = None
    stderr: IO[bytes] | None = None

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

    def terminate(self, **_kwargs: object) -> None:
        pass

    def kill(self, **_kwargs: object) -> None:
        pass

    def note_external_returncode(self, rc: int) -> None:
        if self._callback is not None:
            self._callback.append(rc)


def test_escalate_termination_sync_harvests_psutil_returncode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = ProcessManager(policy=_FAST_POLICY)
    record = _make_record(manager, pid=5)
    manager.record_terminal_reason(record.pid, "interactive_completion", "test")

    fake_root = _PsutilProcessWithReturncode(pid=5)
    fake_root._terminated = True
    fake_root.returncode = -15

    fake_psutil = FakePsutil()
    fake_psutil._processes = {5: fake_root}
    manager._psutil = fake_psutil

    note_called_with: list[int] = []
    fake_proc = _FakeSyncProc(note_called_with)

    def fake_wait_procs_with_dead(
        procs: Sequence[object], timeout: float | None = None
    ) -> tuple[list[FakePsutilProcess], list[FakePsutilProcess]]:
        return [fake_root], []

    monkeypatch.setattr(fake_psutil, "wait_procs", fake_wait_procs_with_dead)

    manager._escalate_termination_sync(record, fake_proc, grace_period_s=0.01)
    assert record.returncode == -15
    assert -15 in note_called_with


def test_terminate_psutil_wave_harvests_dead_rc(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = ProcessManager(policy=_FAST_POLICY)
    record = _make_record(manager, pid=11)

    fake_root = _PsutilProcessWithReturncode(pid=11)
    fake_root._terminated = True
    fake_root.returncode = -9

    fake_psutil = FakePsutil()
    fake_psutil._processes = {11: fake_root}

    managed_proc = ManagedProcess.__new__(ManagedProcess)
    managed_proc._proc = _FakeSyncProc()
    managed_proc._record = record
    managed_proc._manager = manager

    def fake_wait_procs_with_dead(
        procs: Sequence[object], timeout: float | None = None
    ) -> tuple[list[FakePsutilProcess], list[FakePsutilProcess]]:
        return [fake_root], []

    monkeypatch.setattr(fake_psutil, "wait_procs", fake_wait_procs_with_dead)

    survivors = managed_proc._terminate_psutil_wave(
        fake_psutil,
        [fake_root],
        grace_period_s=0.0,
    )
    assert record.returncode == -9
    assert len(survivors) == 0


def test_kill_psutil_wave_harvests_dead_rc(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = ProcessManager(policy=_FAST_POLICY)
    record = _make_record(manager, pid=12)

    fake_root = _PsutilProcessWithReturncode(pid=12)
    fake_root._terminated = True
    fake_root.returncode = -15

    fake_psutil = FakePsutil()
    fake_psutil._processes = {12: fake_root}

    managed_proc = ManagedProcess.__new__(ManagedProcess)
    managed_proc._proc = _FakeSyncProc()
    managed_proc._record = record
    managed_proc._manager = manager

    def fake_wait_procs_with_dead(
        procs: Sequence[object], timeout: float | None = None
    ) -> tuple[list[FakePsutilProcess], list[FakePsutilProcess]]:
        return [fake_root], []

    monkeypatch.setattr(fake_psutil, "wait_procs", fake_wait_procs_with_dead)

    survivors = managed_proc._kill_psutil_wave(
        fake_psutil,
        [fake_root],
    )
    assert record.returncode == -15
    assert len(survivors) == 0


def test_original_terminal_reason_returns_first_recorded() -> None:
    manager = ProcessManager(policy=_FAST_POLICY)
    pid = 99999
    manager.record_terminal_reason(pid, "interactive_completion", "agy")
    manager._record_termination_outcome(pid, "graceful_terminate", "sent")
    manager._record_termination_outcome(pid, "force_kill", "sent")
    assert manager.original_terminal_reason(pid) == "interactive_completion"
    assert manager.termination_issuer(pid) == "sent"


class _FakePtyProc:
    def __init__(self, pid: int = 100, callback: list[int] | None = None) -> None:
        self.pid = pid
        self._callback = callback
        self._rc: int | None = None
        self.master_fd = -1
        self.slave_fd = -1

    @property
    def returncode(self) -> int | None:
        return self._rc

    def poll(self) -> int | None:
        return self._rc

    def wait(self, timeout: float | None = None) -> int:
        return self._rc if self._rc is not None else 0

    def terminate(self, **_kwargs: object) -> None:
        pass

    def kill(self, **_kwargs: object) -> None:
        pass

    def close(self) -> None:
        pass

    def fileno(self) -> int:
        return -1

    def isatty(self) -> bool:
        return False

    def note_external_returncode(self, rc: int) -> None:
        if self._rc is None:
            self._rc = rc
        if self._callback is not None:
            self._callback.append(rc)


class _FakeAsyncProcWithNote:
    def __init__(self, pid: int = 5, callback: list[int] | None = None) -> None:
        self.pid = pid
        self._callback = callback
        self._rc: int | None = None

    @property
    def stdin(self) -> asyncio.StreamWriter | None:
        return None

    @property
    def stdout(self) -> asyncio.StreamReader | None:
        return None

    @property
    def stderr(self) -> asyncio.StreamReader | None:
        return None

    @property
    def returncode(self) -> int | None:
        return self._rc

    def poll(self) -> int | None:
        return self._rc

    async def wait(self) -> int:
        return self._rc if self._rc is not None else 0

    async def communicate(self, input: bytes | None = None) -> tuple[bytes | None, bytes | None]:
        return None, None

    def terminate(self, **_kwargs: object) -> None:
        pass

    def kill(self, **_kwargs: object) -> None:
        pass

    def note_external_returncode(self, rc: int) -> None:
        if self._rc is None:
            self._rc = rc
        if self._callback is not None:
            self._callback.append(rc)


def test_harvest_and_mark_descendant_unrecorded_descendant_leaves_parent_untouched() -> None:
    manager = ProcessManager(policy=_FAST_POLICY)
    parent_record = _make_record(manager, pid=100)
    parent_called_with: list[int] = []
    parent_proc = _FakeSyncProc(parent_called_with)
    parent_proc.pid = 100
    manager._sync_procs[100] = parent_proc

    fake_child = _PsutilProcessWithReturncode(pid=200)
    fake_child._terminated = True
    fake_child.returncode = -9

    manager._harvest_and_mark_descendant(
        descendant_pid=200,
        gone=[fake_child],
    )

    assert parent_record.returncode is None
    assert parent_record.status == ProcessStatus.RUNNING
    assert parent_called_with == []
    assert parent_proc.returncode is None
    assert 200 not in {record.pid for record in manager.list_records()}


def test_harvest_and_mark_descendant_recorded_descendant_updates_only_itself() -> None:
    manager = ProcessManager(policy=_FAST_POLICY)
    parent_record = _make_record(manager, pid=100)
    parent_called_with: list[int] = []
    parent_proc = _FakeSyncProc(parent_called_with)
    parent_proc.pid = 100
    manager._sync_procs[100] = parent_proc

    child_record = _make_record(manager, pid=200)
    child_called_with: list[int] = []
    child_proc = _FakeSyncProc(child_called_with)
    child_proc.pid = 200
    manager._sync_procs[200] = child_proc

    fake_child = _PsutilProcessWithReturncode(pid=200)
    fake_child._terminated = True
    fake_child.returncode = -9

    manager._harvest_and_mark_descendant(
        descendant_pid=200,
        gone=[fake_child],
    )

    assert child_record.returncode == -9
    assert child_record.status == ProcessStatus.KILLED
    assert child_called_with == [-9]

    assert parent_record.returncode is None
    assert parent_record.status == ProcessStatus.RUNNING
    assert parent_called_with == []


def test_unrecorded_descendant_leaves_parent_pty_untouched() -> None:
    manager = ProcessManager(policy=_FAST_POLICY)
    parent_record = _make_record(manager, pid=100)
    fake_pty = _FakePtyProc(pid=100)
    manager._pty_procs[100] = fake_pty
    managed_pty = ManagedPtyProcess(fake_pty, parent_record, manager)

    fake_child = _PsutilProcessWithReturncode(pid=200)
    fake_child._terminated = True
    fake_child.returncode = -9

    manager._harvest_and_mark_descendant(
        descendant_pid=200,
        gone=[fake_child],
    )

    assert parent_record.returncode is None
    assert managed_pty.returncode is None
    assert fake_pty.returncode is None


def test_harvest_and_mark_descendant_ignores_mismatched_pid_in_gone() -> None:
    manager = ProcessManager(policy=_FAST_POLICY)
    parent_record = _make_record(manager, pid=100)
    child_record = _make_record(manager, pid=200)

    fake_unrelated = _PsutilProcessWithReturncode(pid=300)
    fake_unrelated._terminated = True
    fake_unrelated.returncode = -15

    manager._harvest_and_mark_descendant(
        descendant_pid=200,
        gone=[fake_unrelated],
    )

    assert child_record.returncode is None
    assert child_record.status == ProcessStatus.RUNNING
    assert parent_record.returncode is None


def test_terminate_registered_descendants_propagation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = ProcessManager(policy=_FAST_POLICY)
    parent_record = _make_record(manager, pid=100)
    parent_called_with: list[int] = []
    parent_proc = _FakeSyncProc(parent_called_with)
    parent_proc.pid = 100
    manager._sync_procs[100] = parent_proc

    child_recorded = _make_record(manager, pid=202)
    child_called_with: list[int] = []
    child_proc = _FakeSyncProc(child_called_with)
    child_proc.pid = 202
    manager._sync_procs[202] = child_proc

    manager.register_descendant(parent_pid=100, descendant_pid=201)
    manager.register_descendant(parent_pid=100, descendant_pid=202)

    fake_unrecorded = _PsutilProcessWithReturncode(pid=201)
    fake_unrecorded._terminated = True
    fake_unrecorded.returncode = -9

    fake_recorded = _PsutilProcessWithReturncode(pid=202)
    fake_recorded._terminated = True
    fake_recorded.returncode = -15

    fake_psutil = FakePsutil()
    fake_psutil._processes = {201: fake_unrecorded, 202: fake_recorded}
    manager._psutil = fake_psutil

    def fake_wait_procs(
        procs: Sequence[object], timeout: float | None = None
    ) -> tuple[list[FakePsutilProcess], list[FakePsutilProcess]]:
        return [p for p in procs if isinstance(p, FakePsutilProcess)], []

    monkeypatch.setattr(fake_psutil, "wait_procs", fake_wait_procs)

    terminated = manager._terminate_registered_descendants(parent_record, grace_period_s=0.01)

    assert set(terminated) == {201, 202}
    assert child_recorded.returncode == -15
    assert child_recorded.status == ProcessStatus.KILLED
    assert child_called_with == [-15]
    assert parent_record.returncode is None
    assert parent_called_with == []


@pytest.mark.asyncio
async def test_escalate_termination_async_harvests_psutil_returncode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = ProcessManager(policy=_FAST_POLICY)
    record = _make_record(manager, pid=50)
    manager.record_terminal_reason(record.pid, "interactive_completion", "test")

    fake_root = _PsutilProcessWithReturncode(pid=50)
    fake_root._terminated = False
    fake_root.returncode = -15

    fake_psutil = FakePsutil()
    fake_psutil._processes = {50: fake_root}
    manager._psutil = fake_psutil

    note_called_with: list[int] = []
    fake_proc = _FakeAsyncProcWithNote(pid=50, callback=note_called_with)

    def fake_wait_procs_with_dead(
        procs: Sequence[object], timeout: float | None = None
    ) -> tuple[list[FakePsutilProcess], list[FakePsutilProcess]]:
        return [fake_root], []

    monkeypatch.setattr(fake_psutil, "wait_procs", fake_wait_procs_with_dead)

    try:
        await manager._escalate_termination_async(record, fake_proc, grace_period_s=0.01)
        assert record.returncode == -15
        assert -15 in note_called_with
        assert fake_proc.returncode == -15
    finally:
        manager.shutdown_all(grace_period_s=0.0)


def test_escalate_with_psutil_harvests_psutil_returncode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = ProcessManager(policy=_FAST_POLICY)
    record = _make_record(manager, pid=60)
    manager.record_terminal_reason(record.pid, "interactive_completion", "test")

    fake_root = _PsutilProcessWithReturncode(pid=60)
    fake_root._terminated = True
    fake_root.returncode = -9

    fake_psutil = FakePsutil()
    fake_psutil._processes = {60: fake_root}
    manager._psutil = fake_psutil

    note_called_with: list[int] = []
    fake_proc = _FakeAsyncProcWithNote(pid=60, callback=note_called_with)

    def fake_wait_procs_with_dead(
        procs: Sequence[object], timeout: float | None = None
    ) -> tuple[list[FakePsutilProcess], list[FakePsutilProcess]]:
        return [fake_root], []

    monkeypatch.setattr(fake_psutil, "wait_procs", fake_wait_procs_with_dead)

    manager._escalate_with_psutil(record, fake_proc, fake_psutil, grace_period_s=0.01)
    assert record.returncode == -9
    assert -9 in note_called_with
    assert fake_proc.returncode == -9


def test_escalate_termination_sync_cross_harvests_owned_child(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = ProcessManager(policy=_FAST_POLICY)
    parent_record = _make_record(manager, pid=100)
    child_record = _make_record(manager, pid=202)
    manager.record_terminal_reason(parent_record.pid, "interactive_completion", "test")

    parent_proc = _FakeSyncProc()
    parent_proc.pid = 100
    manager._sync_procs[100] = parent_proc
    parent_handle = ManagedProcess(parent_proc, parent_record, manager)

    child_proc = _FakeSyncProc()
    child_proc.pid = 202
    manager._sync_procs[202] = child_proc
    child_handle = ManagedProcess(child_proc, child_record, manager)

    fake_child = _PsutilProcessWithReturncode(pid=202)
    fake_child._terminated = True
    fake_child.returncode = -15

    fake_parent = _PsutilProcessWithReturncode(pid=100, _children=[fake_child])
    fake_parent._terminated = True
    fake_parent.returncode = -15

    fake_psutil = FakePsutil()
    fake_psutil._processes = {100: fake_parent, 202: fake_child}
    manager._psutil = fake_psutil

    def fake_wait_procs_with_dead(
        procs: Sequence[object], timeout: float | None = None
    ) -> tuple[list[FakePsutilProcess], list[FakePsutilProcess]]:
        return [fake_parent, fake_child], []

    monkeypatch.setattr(fake_psutil, "wait_procs", fake_wait_procs_with_dead)

    manager._escalate_termination_sync(parent_record, parent_proc, grace_period_s=0.01)

    assert parent_record.status == ProcessStatus.KILLED
    assert parent_record.returncode == -15
    assert child_record.status == ProcessStatus.KILLED
    assert child_record.returncode == -15

    assert parent_handle.returncode == -15
    assert child_handle.returncode == -15


def test_terminate_by_pid_persists_harvested_root_when_child_zombie(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = ProcessManager(policy=_FAST_POLICY)
    record = _make_record(manager, pid=10)

    fake_child = _PsutilProcessWithReturncode(pid=20)
    fake_child.returncode = -9

    fake_root = _PsutilProcessWithReturncode(pid=10, _children=[fake_child])
    fake_root._terminated = True
    fake_root.returncode = -15

    fake_psutil = FakePsutil()
    fake_psutil._processes = {10: fake_root, 20: fake_child}
    manager._psutil = fake_psutil

    wave = 0

    def fake_wait_procs(
        procs: Sequence[object], timeout: float | None = None
    ) -> tuple[list[FakePsutilProcess], list[FakePsutilProcess]]:
        nonlocal wave
        if timeout == 0.0:
            return [fake_child], []
        wave += 1
        if wave == 1:
            return [fake_root], [fake_child]
        return [], [fake_child]

    monkeypatch.setattr(fake_psutil, "wait_procs", fake_wait_procs)

    manager._terminate_by_pid(record, grace_period_s=0.01)

    assert record.status == ProcessStatus.KILLED
    assert record.returncode == -15


def test_escalate_termination_sync_persists_harvested_root_when_child_zombie(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = ProcessManager(policy=_FAST_POLICY)
    parent_record = _make_record(manager, pid=30)
    manager.record_terminal_reason(parent_record.pid, "interactive_completion", "test")

    parent_proc = _FakeSyncProc()
    parent_proc.pid = 30
    manager._sync_procs[30] = parent_proc
    parent_handle = ManagedProcess(parent_proc, parent_record, manager)

    fake_child = _PsutilProcessWithReturncode(pid=40)
    fake_child.returncode = -9

    fake_parent = _PsutilProcessWithReturncode(pid=30, _children=[fake_child])
    fake_parent._terminated = True
    fake_parent.returncode = -15

    fake_psutil = FakePsutil()
    fake_psutil._processes = {30: fake_parent, 40: fake_child}
    manager._psutil = fake_psutil

    wave = 0

    def fake_wait_procs(
        procs: Sequence[object], timeout: float | None = None
    ) -> tuple[list[FakePsutilProcess], list[FakePsutilProcess]]:
        nonlocal wave
        if timeout == 0.0:
            return [fake_child], []
        wave += 1
        if wave == 1:
            return [fake_parent], [fake_child]
        return [], [fake_child]

    monkeypatch.setattr(fake_psutil, "wait_procs", fake_wait_procs)

    manager._escalate_termination_sync(parent_record, parent_proc, grace_period_s=0.01)

    assert parent_record.status == ProcessStatus.KILLED
    assert parent_record.returncode == -15
    assert parent_handle.returncode == -15
