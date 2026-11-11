from __future__ import annotations

import asyncio
import itertools
import os
from datetime import UTC, datetime
from unittest.mock import patch

from ralph.process import (
    ProcessManager,
    ProcessManagerPolicy,
    ProcessStatus,
    SpawnOptions,
)
from ralph.process.manager import _process_manager as pm_mod
from ralph.process.manager._process_liveness import LivenessResult
from ralph.process.manager._process_record import ProcessRecord
from ralph.testing._fake_async_process import FakeAsyncProcess
from ralph.testing._process_state import ProcessState
from ralph.testing.fake_process import FakePsutil, FakePsutilProcess


class _ZombieFakePsutilProcess(FakePsutilProcess):
    def __init__(self, pid: int) -> None:
        super().__init__(pid=pid)
        self._reaped = False

    def is_running(self) -> bool:
        if self._reaped:
            return False
        if self._killed or self._terminated:
            return True
        return super().is_running()

    def status(self) -> str:
        if self._reaped:
            return "running"
        if self._killed or self._terminated:
            return "zombie"
        return str(self._status)


class _ZombieFakePsutil(FakePsutil):
    def __init__(self) -> None:
        super().__init__()
        self._reaped_pids: set[int] = set()

    def process_from_pid(self, pid: int) -> FakePsutilProcess:
        if pid not in self._processes:
            self._processes[pid] = _ZombieFakePsutilProcess(pid=pid)
        proc = self._processes[pid]
        assert isinstance(proc, _ZombieFakePsutilProcess)
        return proc

    def pid_exists(self, pid: int) -> bool:
        if pid in self._reaped_pids:
            return False
        if pid not in self._processes:
            return False
        return self._processes[pid].is_running()

    def reap_zombie(self, proc: _ZombieFakePsutilProcess) -> None:
        proc._reaped = True
        self._reaped_pids.add(proc.pid)

    @property
    def reaped_pids(self) -> set[int]:
        return set(self._reaped_pids)


def _make_pm_with_psutil(psutil_mod: _ZombieFakePsutil) -> ProcessManager:
    pid_iter = itertools.count(5000)

    def _sync_factory(command: object, opts: object) -> _ZombieFakePsutilProcess:
        del command, opts
        return _ZombieFakePsutilProcess(pid=next(pid_iter))

    return ProcessManager(
        sync_process_factory=_sync_factory,
        psutil=psutil_mod,
        policy=ProcessManagerPolicy(
            default_grace_period_s=0.0,
            kill_followup_timeout_s=0.0,
            log_events=False,
            enable_zombie_reaper=False,
        ),
    )


def test_terminate_by_pid_leaves_no_unreaped_zombie_records() -> None:
    pid = 5001
    psutil_mod = _ZombieFakePsutil()
    pm = _make_pm_with_psutil(psutil_mod)

    managed = pm.spawn(("fake", "zombie"), SpawnOptions())
    pid = managed.pid

    psutil_proc = psutil_mod.process_from_pid(pid)
    assert psutil_proc.is_running() is True
    assert psutil_proc.status() != "zombie"

    record = managed.record

    real_verify = pm_mod.verify_process_liveness

    def _fake_verify(
        p: int,
        *,
        psutil_mod: _ZombieFakePsutil | None = None,
    ) -> LivenessResult:
        if p == pid:
            return LivenessResult.ZOMBIE
        return real_verify(p, psutil_mod=psutil_mod)

    real_waitpid = os.waitpid if hasattr(os, "waitpid") else None
    waitpid_calls: list[tuple[int, int]] = []

    def _fake_waitpid(wait_pid: int, options: int) -> tuple[int, int]:
        waitpid_calls.append((wait_pid, options))
        psutil_mod.reap_zombie(psutil_proc)
        if real_waitpid is not None:
            try:
                return real_waitpid(wait_pid, options)
            except (OSError, ChildProcessError, ProcessLookupError):
                return (0, 0)
        return (0, 0)

    with (
        patch.object(pm_mod, "verify_process_liveness", _fake_verify),
        patch.object(os, "waitpid", _fake_waitpid, create=True),
    ):
        pm._terminate_by_pid(record, grace_period_s=0.0)

    assert pm.list_active() == [], (
        f"Expected no active records after terminate; got {pm.list_active()}"
    )
    assert record.status == ProcessStatus.KILLED, f"Record must be KILLED; got {record.status}"
    assert record.cause == "zombie_after_kill", (
        f"Record.cause must be 'zombie_after_kill'; got {record.cause!r}"
    )

    assert waitpid_calls, f"Expected os.waitpid to be called; got calls: {waitpid_calls}"
    assert pid in psutil_mod.reaped_pids, (
        f"PID {pid} must be marked reaped; got {psutil_mod.reaped_pids}"
    )
    assert psutil_mod.pid_exists(pid) is False, "Reaped PID must not be visible to pid_exists()"


def test_terminate_root_only_async_zombie_path() -> None:
    pid = 6001
    psutil_mod = _ZombieFakePsutil()
    pm = _make_pm_with_psutil(psutil_mod)
    psutil_mod.process_from_pid(pid)

    record = ProcessRecord(
        pid=pid,
        pgid=pid,
        command=("fake", "async"),
        cwd=None,
        started_at=datetime.now(tz=UTC),
        status=ProcessStatus.RUNNING,
    )

    proc = FakeAsyncProcess(
        pid=pid,
        state=ProcessState(killed=True),
    )

    real_verify = pm_mod.verify_process_liveness

    def _fake_verify(
        p: int,
        *,
        psutil_mod: _ZombieFakePsutil | None = None,
    ) -> LivenessResult:
        if p == pid:
            return LivenessResult.ZOMBIE
        return real_verify(p, psutil_mod=psutil_mod)

    async def _drive() -> None:
        with patch.object(pm_mod, "verify_process_liveness", _fake_verify):
            await pm._terminate_root_only_async(record, proc, grace_period_s=0.0)

    asyncio.run(_drive())

    assert record.status == ProcessStatus.KILLED
    assert record.cause == "zombie_after_kill"
    assert pm.list_active() == []


def test_terminate_by_pid_does_not_leave_unreaped_zombie_in_psutil_table() -> None:
    psutil_mod = _ZombieFakePsutil()
    pm = _make_pm_with_psutil(psutil_mod)

    pid_iter = itertools.count(7000)

    def _fake_verify(
        p: int,
        *,
        psutil_mod: _ZombieFakePsutil | None = None,
    ) -> LivenessResult:
        del p, psutil_mod
        return LivenessResult.ZOMBIE

    real_waitpid = os.waitpid if hasattr(os, "waitpid") else None
    reaped: set[int] = set()

    def _fake_waitpid(wait_pid: int, options: int) -> tuple[int, int]:
        reaped.add(wait_pid)
        if real_waitpid is not None:
            try:
                return real_waitpid(wait_pid, options)
            except (OSError, ChildProcessError, ProcessLookupError):
                return (0, 0)
        return (0, 0)

    with (
        patch.object(pm_mod, "verify_process_liveness", _fake_verify),
        patch.object(os, "waitpid", _fake_waitpid, create=True),
    ):
        for _ in range(3):
            pid = next(pid_iter)
            managed = pm.spawn(("fake", "zombie"), SpawnOptions())
            pid = managed.pid
            psutil_mod.process_from_pid(pid)
            record = managed.record

            pm._terminate_by_pid(record, grace_period_s=0.0)

            assert record.status == ProcessStatus.KILLED
            assert record.cause == "zombie_after_kill"
            assert pid in reaped, f"PID {pid} must be reaped via os.waitpid; reaped={reaped}"
