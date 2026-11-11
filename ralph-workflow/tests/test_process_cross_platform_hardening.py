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
    purge_on_init=False,
    enable_zombie_reaper=False,
)


def _make_pm(
    *,
    sync_factory: object = None,
    psutil_mod: FakePsutil | None = None,
) -> ProcessManager:
    if psutil_mod is None:
        psutil_mod = FakePsutil()
    return ProcessManager(
        policy=_FAST_POLICY,
        sync_process_factory=sync_factory or make_sync_process_factory(itertools.count(1)),
        async_process_factory=make_async_process_factory(itertools.count(100)),
        psutil=psutil_mod,
    )


def test_psutil_none_fallback_terminate_escalates() -> None:
    pm = _make_pm(psutil_mod=None)
    handle = pm.spawn([sys.executable, "-c", "pass"])

    handle.terminate(grace_period_s=0.1)

    assert handle.record.status == ProcessStatus.KILLED
    assert handle.record.cause == "killed"


def test_psutil_none_fallback_force_kill_failure() -> None:

    def immortal_factory(*args: object, **kwargs: object) -> FakeImmortalPopen:
        return FakeImmortalPopen(pid=1)

    pm = ProcessManager(
        policy=ProcessManagerPolicy(
            default_grace_period_s=0.1,
            kill_followup_timeout_s=0.1,
            log_events=False,
            purge_on_init=False,
            enable_zombie_reaper=False,
        ),
        sync_process_factory=immortal_factory,
        async_process_factory=make_async_process_factory(itertools.count(100)),
        psutil=None,
    )
    handle = pm.spawn([sys.executable, "-c", "pass"])

    with pytest.raises(ProcessTerminationError):
        handle.terminate(grace_period_s=0.01)

    assert handle.record.status == ProcessStatus.FAILED
    assert handle.record.cause == "termination_failed"


def test_psutil_none_shutdown_all() -> None:
    pm = _make_pm(psutil_mod=None)

    handles = [pm.spawn([sys.executable, "-c", "pass"]) for _ in range(3)]
    pm.shutdown_all(grace_period_s=0.1)

    for h in handles:
        assert h.record.status == ProcessStatus.KILLED
    assert pm.list_active() == []


def test_os_getpgid_unavailable_fallback_to_pid() -> None:
    sync_factory = make_sync_process_factory(itertools.count(1), returncode=0)
    pm = ProcessManager(
        policy=_FAST_POLICY,
        sync_process_factory=sync_factory,
        async_process_factory=make_async_process_factory(itertools.count(100)),
    )

    with patch("builtins.hasattr") as mock_hasattr:
        _orig_hasattr = hasattr

        def _fake_hasattr(obj: object, name: str) -> bool:
            if obj is os and name == "getpgid":
                return False
            return _orig_hasattr(obj, name)

        mock_hasattr.side_effect = _fake_hasattr
        handle = pm.spawn([sys.executable, "-c", "pass"])

    assert handle.record.pgid == handle.record.pid


def test_start_new_session_isolates_process_group() -> None:
    sync_factory = make_sync_process_factory(itertools.count(1), returncode=None)
    pm = _make_pm(sync_factory=sync_factory, psutil_mod=FakePsutil())

    handle = pm.spawn(
        [sys.executable, "-c", "pass"],
    )
    handle._record.pid = 100
    handle._record.pgid = 100

    assert handle.record.pgid == handle.record.pid


def test_os_killpg_unavailable_uses_os_kill_fallback() -> None:
    sync_factory = make_sync_process_factory(itertools.count(1), returncode=None)
    pm = _make_pm(sync_factory=sync_factory, psutil_mod=FakePsutil())
    handle = pm.spawn([sys.executable, "-c", "pass"])

    with patch.object(os, "killpg", None):
        pm.shutdown_all(grace_period_s=0.1)

    assert handle.record.status == ProcessStatus.KILLED


def test_descendant_registry_cleanup_on_psutil_none() -> None:
    pm = _make_pm(psutil_mod=None)
    handle = pm.spawn([sys.executable, "-c", "pass"])

    pm.register_descendant(handle.pid, 99999)
    assert 99999 in pm._descendants.get(handle.pid, [])

    handle.terminate(grace_period_s=0.1)
    assert handle.pid not in pm._descendants


def test_descendant_registry_with_psutil_available() -> None:
    sync_factory = make_sync_process_factory(itertools.count(1), returncode=None)

    parent_pid = 1
    child = FakePsutilProcess(pid=1001, _running=True, _status="sleeping", _create_time=0.0)

    class _RootWithChild(FakePsutilProcess):
        def children(self, recursive: bool = False) -> list[FakePsutilProcess]:
            return [child]

    root = _RootWithChild(pid=parent_pid)
    fake_psutil = FakePsutil()
    fake_psutil._processes = {parent_pid: root, 1001: child}

    pm = _make_pm(sync_factory=sync_factory, psutil_mod=fake_psutil)
    handle = pm.spawn([sys.executable, "-c", "pass"])
    handle._record.pid = parent_pid

    pm.register_descendant(parent_pid, 1001)
    pm.shutdown_all(grace_period_s=0.1)

    assert handle.record.status == ProcessStatus.KILLED
    assert parent_pid not in pm._descendants


def test_termination_outcomes_logged_across_platforms() -> None:
    sync_factory = make_sync_process_factory(itertools.count(1), returncode=None)
    pm = _make_pm(sync_factory=sync_factory, psutil_mod=FakePsutil())
    handle = pm.spawn([sys.executable, "-c", "pass"])

    handle.terminate(grace_period_s=0.1)

    outcomes = pm.list_termination_outcomes()
    assert handle.pid in outcomes
    stages = [o["stage"] for o in outcomes[handle.pid]]
    assert "graceful_terminate" in stages or "force_kill" in stages
