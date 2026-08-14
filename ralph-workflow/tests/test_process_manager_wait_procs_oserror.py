"""Regression tests for the psutil ``wait_procs`` OSError (pidfd_open EINVAL) crash.

psutil's ``wait_procs`` internally calls ``os.pidfd_open(pid, 0)`` per process
and raises ``OSError: [Errno 22] Invalid argument`` when the PID was already
terminated/reaped mid-wait. Before the ``_safe_wait_procs`` helper, that
OSError propagated out of the termination paths and aborted the teardown
(see the traceback in ``.agent/PRODUCT_CRITERIA.md``). These tests simulate
the exact crash and assert the termination still completes truthfully.
"""

from __future__ import annotations

import errno
import itertools
import sys

import pytest

from ralph.process.manager import ProcessManager, ProcessManagerPolicy
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


class _EinvalWaitPsutil(FakePsutil):
    """Fake psutil whose ``wait_procs`` always raises the pidfd_open EINVAL OSError."""

    def __init__(self) -> None:
        super().__init__()
        self.wait_procs_calls = 0

    def wait_procs(
        self,
        procs: list[FakePsutilProcess],
        timeout: float | None = None,
    ) -> tuple[list[FakePsutilProcess], list[FakePsutilProcess]]:
        del timeout
        self.wait_procs_calls += 1
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
    """The traceback path must terminate truthfully despite the pidfd_open OSError."""
    pm, fake_psutil = _make_manager()
    handle = pm.spawn([sys.executable, "-c", "pass"])

    handle.terminate(grace_period_s=0.0)

    assert handle.record.status == ProcessStatus.KILLED
    assert pm.list_active() == []
    # The fix routes wait_procs through _safe_wait_procs; the fake must still
    # have been called (the helper classifies per-PID, it does not skip the wait).
    assert fake_psutil.wait_procs_calls >= 1


def test_terminate_by_pid_survives_wait_procs_einval() -> None:
    """The symmetric _terminate_by_pid path must terminate truthfully too."""
    pm, fake_psutil = _make_manager()
    handle = pm.spawn([sys.executable, "-c", "pass"])

    pm._terminate_by_pid(handle.record, grace_period_s=0.0)

    assert handle.record.status == ProcessStatus.KILLED
    assert pm.list_active() == []
    assert fake_psutil.wait_procs_calls >= 1


def test_safe_wait_procs_fallback_keeps_unprovable_procs_alive() -> None:
    """On OSError the helper must classify each proc by its own PID.

    A proc whose liveness is ALIVE/UNKNOWN must come back in ``alive`` so the
    caller retries it — a batched-wait failure can never look like success.
    """
    pm, fake_psutil = _make_manager()
    root = FakePsutilProcess(pid=1)  # never terminated → liveness ALIVE
    dead = FakePsutilProcess(pid=2)
    dead.terminate()  # liveness ZOMBIE
    fake_psutil._processes = {1: root, 2: dead}

    gone, alive = pm._safe_wait_procs(pm._psutil, [root, dead], timeout=0.0)

    assert alive == [root]
    assert gone == [dead]


@pytest.mark.parametrize("grace", [0.0, 0.01], ids=["zero-grace", "short-grace"])
def test_escalate_termination_sync_parametrized_einval(grace: float) -> None:
    """Parametrized regression: every grace window survives the EINVAL crash."""
    pm, fake_psutil = _make_manager()
    handle = pm.spawn([sys.executable, "-c", "pass"])

    handle.terminate(grace_period_s=grace)

    assert handle.record.status == ProcessStatus.KILLED
    assert fake_psutil.wait_procs_calls >= 1
