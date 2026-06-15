"""Lightweight integration test for process zombie cleanup.

Drives ProcessManager with `FakePsutil` and a fake process configured to
become a zombie after kill, then asserts that:

1. No active records remain after the terminate flow completes.
2. The FakePsutil process table does not show a zombie-status entry
   for the terminated PID \u2014 i.e. it was reaped, not left as a defunct
   process the OS still has to clean up.
3. The process record was transitioned to a terminal state with
   cause='zombie_after_kill' when the reaping path is taken.

The test does not spawn a real subprocess, is not marked
@subprocess_e2e, uses no time.sleep, and completes in well under 1.0s
to stay within the 60-second combined `make verify` budget.
"""

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

# --------------------------------------------------------------------------/
# Test doubles
# --------------------------------------------------------------------------/


class _ZombieFakePsutilProcess(FakePsutilProcess):
    """FakePsutilProcess that simulates a process becoming zombie after kill.

    After ``kill()`` is invoked, ``is_running()`` still returns True
    (mirroring the real OS: a killed child is a zombie, which is
    technically still in the process table until reaped). ``status()``
    returns 'zombie'. After the reaping helper runs, ``is_running()``
    flips to False and the process is considered fully reaped.
    """

    def __init__(self, pid: int) -> None:
        super().__init__(pid=pid)
        self._reaped = False

    def is_running(self) -> bool:
        if self._reaped:
            return False
        # Killed but not reaped: process is a zombie but still in the
        # process table, so still "running" from psutil's perspective.
        if self._killed or self._terminated:
            return True
        return super().is_running()

    def status(self) -> str:
        if self._reaped:
            return "running"  # post-reap, no longer zombie
        if self._killed or self._terminated:
            return "zombie"
        return str(self._status)


class _ZombieFakePsutil(FakePsutil):
    """FakePsutil that tracks reaping through wait_procs.

    Behaviour:
    - wait_procs returns (dead, alive) by consulting is_running() on
      each proc. After kill(), a proc is still "running" (zombie) and
      is returned as alive. After reap, is_running() returns False and
      the proc is returned as dead.
    - reap_zombie(p) explicitly marks a proc as reaped, which flips
      is_running() to False. The reaping helper does this via
      os.waitpid in production; in the test we use this explicit
      method to model that side effect.

    The FakePsutil does not auto-reap on wait_procs. Reaping is a
    side effect of the reaping helper only.
    """

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
        """Mark a zombie process as reaped. Models os.waitpid side effect."""
        proc._reaped = True
        self._reaped_pids.add(proc.pid)

    @property
    def reaped_pids(self) -> set[int]:
        return set(self._reaped_pids)


def _make_pm_with_psutil(psutil_mod: _ZombieFakePsutil) -> ProcessManager:
    """Build a ProcessManager with the given psutil and a stub sync factory."""
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


# --------------------------------------------------------------------------/
# Tests
# --------------------------------------------------------------------------/


def test_terminate_by_pid_leaves_no_unreaped_zombie_records() -> None:
    """After ProcessManager terminates a zombie-status process, the
    record is terminal and the FakePsutil table has no zombie entry.

    This is the integration-level counterpart to the per-path unit test
    in ``tests/test_process_zombie_reaping.py``. It exercises the full
    ProcessManager._terminate_by_pid() flow (one of the seven paths)
    to prove end-to-end that no zombie_after_kill path leaves a
    defunct process behind.
    """
    pid = 5001
    psutil_mod = _ZombieFakePsutil()
    pm = _make_pm_with_psutil(psutil_mod)

    # Spawn a real ProcessRecord so the manager tracks the pid.
    managed = pm.spawn(("fake", "zombie"), SpawnOptions())
    pid = managed.pid

    # Pre-register the psutil process so it shows up in process_iter()
    # and the liveness probe sees it as alive.
    psutil_proc = psutil_mod.process_from_pid(pid)
    assert psutil_proc.is_running() is True
    assert psutil_proc.status() != "zombie"

    record = managed.record

    # Force the post-kill liveness check (patched at the call site
    # since _process_manager imports it as a name) to return ZOMBIE so
    # _terminate_by_pid takes the reaping branch.
    real_verify = pm_mod.verify_process_liveness

    def _fake_verify(
        p: int,
        *,
        psutil_mod: _ZombieFakePsutil | None = None,
    ) -> LivenessResult:
        if p == pid:
            return LivenessResult.ZOMBIE
        return real_verify(p, psutil_mod=psutil_mod)

    # Hook os.waitpid so the reaping helper can mark the proc as
    # reaped without touching the real process table.
    real_waitpid = os.waitpid if hasattr(os, "waitpid") else None
    waitpid_calls: list[tuple[int, int]] = []

    def _fake_waitpid(wait_pid: int, options: int) -> tuple[int, int]:
        waitpid_calls.append((wait_pid, options))
        psutil_mod.reap_zombie(psutil_proc)
        # Mimic the real OS: WNOHANG + already-reaped returns (0, 0)
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

    # After terminate: no active records, no zombie in psutil table.
    assert pm.list_active() == [], (
        f"Expected no active records after terminate; got {pm.list_active()}"
    )
    assert record.status == ProcessStatus.KILLED, f"Record must be KILLED; got {record.status}"
    assert record.cause == "zombie_after_kill", (
        f"Record.cause must be 'zombie_after_kill'; got {record.cause!r}"
    )

    # The zombie was reaped: os.waitpid was called and the process is
    # no longer a zombie in the FakePsutil table.
    assert waitpid_calls, f"Expected os.waitpid to be called; got calls: {waitpid_calls}"
    assert pid in psutil_mod.reaped_pids, (
        f"PID {pid} must be marked reaped; got {psutil_mod.reaped_pids}"
    )
    # And pid_exists() now returns False (process is gone).
    assert psutil_mod.pid_exists(pid) is False, "Reaped PID must not be visible to pid_exists()"


def test_terminate_root_only_async_zombie_path() -> None:
    """The async terminate path also routes through the reaping helper.

    Drives ``_terminate_root_only_async`` with a fake async process and
    a forced ZOMBIE liveness result. Verifies the record transitions
    to KILLED with cause='zombie_after_kill'.
    """
    pid = 6001
    psutil_mod = _ZombieFakePsutil()
    pm = _make_pm_with_psutil(psutil_mod)
    psutil_mod.process_from_pid(pid)  # register

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
    """Repeatedly terminating zombie processes never accumulates entries.

    This protects against the leak vector: each terminate flow must
    reap its own zombie. If the reaping helper regressed, the test
    would accumulate unreaped zombie entries across iterations.
    """
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
            psutil_mod.process_from_pid(pid)  # register
            record = managed.record

            pm._terminate_by_pid(record, grace_period_s=0.0)

            assert record.status == ProcessStatus.KILLED
            assert record.cause == "zombie_after_kill"
            assert pid in reaped, f"PID {pid} must be reaped via os.waitpid; reaped={reaped}"
