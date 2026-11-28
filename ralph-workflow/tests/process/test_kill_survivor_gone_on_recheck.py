from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

import pytest

from ralph.process.manager import (
    LivenessResult,
    ProcessManager,
    ProcessManagerPolicy,
    ProcessRecord,
    ProcessStatus,
)
from ralph.testing.fake_process import FakePsutil, FakePsutilProcess

pytestmark = pytest.mark.timeout_seconds(5)

_FAST_POLICY = ProcessManagerPolicy(
    default_grace_period_s=0.0,
    kill_followup_timeout_s=0.0,
    log_events=False,
    enable_zombie_reaper=False,
)


class _SlowToDiePsutil(FakePsutil):
    """Reports every process as still running when the kill follow-up wait times out."""

    def wait_procs(
        self,
        procs: Sequence[object],
        timeout: float | None = None,
    ) -> tuple[list[FakePsutilProcess], list[FakePsutilProcess]]:
        del timeout
        return [], [proc for proc in procs if isinstance(proc, FakePsutilProcess)]


class _AliveUntilRechecked:
    """The process is alive when termination starts and gone by the post-kill re-check."""

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, pid: int, *, psutil_mod: object | None = None) -> LivenessResult:
        del pid, psutil_mod
        self.calls += 1
        return LivenessResult.ALIVE if self.calls == 1 else LivenessResult.GONE


def test_survivor_that_exits_before_the_recheck_is_a_successful_kill() -> None:
    psutil = _SlowToDiePsutil()
    psutil._processes = {11: FakePsutilProcess(pid=11)}
    manager = ProcessManager(
        policy=_FAST_POLICY, psutil=psutil, liveness_verifier=_AliveUntilRechecked()
    )
    record = ProcessRecord(
        pid=11,
        pgid=11,
        command=("mcp-server",),
        cwd=None,
        started_at=datetime.now(tz=UTC),
        status=ProcessStatus.RUNNING,
    )
    manager._records[11] = record

    manager._terminate_by_pid(record, grace_period_s=0.0)

    assert record.status == ProcessStatus.KILLED
    assert record.cause == "gone_after_kill"
