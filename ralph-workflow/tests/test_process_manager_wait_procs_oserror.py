from __future__ import annotations

import errno
import itertools
import sys

import pytest

from ralph.process.manager import (
    ProcessManager,
    ProcessManagerPolicy,
    ProcessTerminationIssuer,
)
from ralph.process.manager._process_status import ProcessStatus
from ralph.testing.fake_process import (
    FakePsutil,
    FakePsutilProcess,
    make_async_process_factory,
    make_sync_process_factory,
)

_FAST_POLICY = ProcessManagerPolicy(
    default_grace_period_s=0.3,
    kill_followup_timeout_s=0.5,
    log_events=False,
    enable_zombie_reaper=False,
)

RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "test_process_manager_regression_registered_descendant_einval_uses_safe_wait": (
        "asserted safe wait behavior through public fake psutil call records rather than "
        "monkeypatching private manager implementation details"
    ),
}


class _EinvalWaitPsutil(FakePsutil):
    def __init__(self) -> None:
        super().__init__()
        self.wait_procs_calls = 0
        self.wait_procs_pids: list[list[int | None]] = []

    def wait_procs(
        self,
        procs: list[FakePsutilProcess],
        timeout: float | None = None,
    ) -> tuple[list[FakePsutilProcess], list[FakePsutilProcess]]:
        del timeout
        self.wait_procs_calls += 1
        self.wait_procs_pids.append([getattr(proc, "pid", None) for proc in procs])
        raise OSError(errno.EINVAL, "Invalid argument")


def _make_manager() -> tuple[ProcessManager, _EinvalWaitPsutil]:
    fake_psutil = _EinvalWaitPsutil()
    pm = ProcessManager(
        policy=_FAST_POLICY,
        sync_process_factory=make_sync_process_factory(itertools.count(1), returncode=None),
        async_process_factory=make_async_process_factory(itertools.count(100)),
        psutil=fake_psutil,
    )
    return pm, fake_psutil


def test_escalate_termination_sync_survives_wait_procs_einval() -> None:
    pm, fake_psutil = _make_manager()
    handle = pm.spawn([sys.executable, "-c", "pass"])

    handle.terminate(grace_period_s=0.0, issuer=ProcessTerminationIssuer.OPERATOR)

    assert handle.record.status == ProcessStatus.KILLED
    assert pm.list_active() == []
    assert fake_psutil.wait_procs_calls >= 1


def test_terminate_by_pid_survives_wait_procs_einval() -> None:
    pm, fake_psutil = _make_manager()
    handle = pm.spawn([sys.executable, "-c", "pass"])

    pm._terminate_by_pid(handle.record, grace_period_s=0.0)

    assert handle.record.status == ProcessStatus.KILLED
    assert pm.list_active() == []
    assert fake_psutil.wait_procs_calls >= 1


def test_safe_wait_procs_fallback_keeps_unprovable_procs_alive() -> None:
    pm, fake_psutil = _make_manager()
    root = FakePsutilProcess(pid=1)
    dead = FakePsutilProcess(pid=2)
    dead.terminate()
    fake_psutil._processes = {1: root, 2: dead}

    gone, alive = pm._safe_wait_procs(pm._psutil, [root, dead], timeout=0.0)

    assert alive == [root]
    assert gone == [dead]


def test_process_manager_regression_registered_descendant_einval_uses_safe_wait() -> None:
    pm, fake_psutil = _make_manager()
    handle = pm.spawn([sys.executable, "-c", "pass"])

    descendant_pid = 4242
    descendant = FakePsutilProcess(pid=descendant_pid)
    fake_psutil._processes[descendant_pid] = descendant
    pm.register_descendant(handle.pid, descendant_pid)

    handle.terminate(grace_period_s=0.0, issuer=ProcessTerminationIssuer.OPERATOR)

    assert handle.record.status == ProcessStatus.KILLED
    assert pm.list_active() == []
    assert not descendant.is_running()
    assert handle.pid not in pm._descendants
    assert fake_psutil.wait_procs_calls >= 1
    assert any(descendant_pid in pids for pids in fake_psutil.wait_procs_pids if pids is not None)


@pytest.mark.parametrize("grace", [0.0, 0.01], ids=["zero-grace", "short-grace"])
def test_escalate_termination_sync_parametrized_einval(grace: float) -> None:
    pm, fake_psutil = _make_manager()
    handle = pm.spawn([sys.executable, "-c", "pass"])

    handle.terminate(grace_period_s=grace, issuer=ProcessTerminationIssuer.OPERATOR)

    assert handle.record.status == ProcessStatus.KILLED
    assert fake_psutil.wait_procs_calls >= 1
