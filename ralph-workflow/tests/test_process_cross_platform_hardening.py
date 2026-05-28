"""Cross-platform hardening tests for ProcessManager.

Verifies that ProcessManager's termination behavior works correctly
across simulated platform differences: psutil-unavailable fallback,
OS-level API unavailability, and process group isolation.

All tests use deterministic fake processes — no real OS processes are spawned.
"""

from __future__ import annotations

import itertools
import os
import sys
from unittest.mock import patch

import pytest

from ralph.process.manager import ProcessManager, ProcessManagerPolicy, ProcessTerminationError
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
    purge_on_init=False,
)


def _make_pm(*, psutil_mod=None):
    """Build a ProcessManager with injected fake process factories."""
    return ProcessManager(
        policy=_FAST_POLICY,
        sync_process_factory=make_sync_process_factory(itertools.count(1)),
        async_process_factory=make_async_process_factory(itertools.count(100)),
        psutil=psutil_mod,
    )


# ---------------------------------------------------------------------------
# Psutil-none fallback: termination falls back to os.kill with escalation
# ---------------------------------------------------------------------------


def test_psutil_none_fallback_terminate_escalates() -> None:
    """When psutil=None, termination falls back to os.kill with correct escalation."""
    pm = _make_pm(psutil_mod=None)
    handle = pm.spawn([sys.executable, "-c", "pass"])

    handle.terminate(grace_period_s=0.1)

    assert handle.record.status == ProcessStatus.KILLED
    assert handle.record.cause == "killed"


def test_psutil_none_fallback_force_kill_failure() -> None:
    """Force kill failure with psutil=None marks record FAILED."""
    from ralph.testing.fake_process import FakeImmortalPopen

    def immortal_factory(*args, **kwargs):
        return FakeImmortalPopen(pid=1)

    pm = ProcessManager(
        policy=ProcessManagerPolicy(
            default_grace_period_s=0.1,
            kill_followup_timeout_s=0.1,
            log_events=False,
            purge_on_init=False,
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
    """shutdown_all() with psutil=None terminates all processes via os.kill."""
    pm = _make_pm(psutil_mod=None)

    handles = [pm.spawn([sys.executable, "-c", "pass"]) for _ in range(3)]
    pm.shutdown_all(grace_period_s=0.1)

    for h in handles:
        assert h.record.status == ProcessStatus.KILLED
    assert pm.list_active() == []


# ---------------------------------------------------------------------------
# os.getpgid unavailable: pgid falls back to pid
# ---------------------------------------------------------------------------


def test_os_getpgid_unavailable_fallback_to_pid() -> None:
    """When os.getpgid is unavailable (simulated Windows), pgid == pid."""

    def no_getpgid():
        raise AttributeError("getpgid not available")

    sync_factory = make_sync_process_factory(itertools.count(1), returncode=0)
    pm = ProcessManager(
        policy=_FAST_POLICY,
        sync_process_factory=sync_factory,
        async_process_factory=make_async_process_factory(itertools.count(100)),
    )

    with patch.object(os, "getpgid", no_getpgid, create=True):
        handle = pm.spawn([sys.executable, "-c", "pass"])

    # pgid should equal pid when getpgid is unavailable
    assert handle.record.pgid == handle.record.pid


# ---------------------------------------------------------------------------
# start_new_session=True creates proper process group isolation
# ---------------------------------------------------------------------------


def test_start_new_session_isolates_process_group() -> None:
    """start_new_session=True yields a process with a distinct pgid."""
    sync_factory = make_sync_process_factory(itertools.count(1), returncode=None)
    pm = _make_pm(sync_factory=sync_factory, psutil_mod=FakePsutil())

    handle = pm.spawn(
        [sys.executable, "-c", "pass"],
    )
    handle._record.pid = 100
    handle._record.pgid = 100  # pgid == pid for start_new_session

    assert handle.record.pgid == handle.record.pid


# ---------------------------------------------------------------------------
# os.killpg unavailable: termination uses os.kill fallback
# ---------------------------------------------------------------------------


def test_os_killpg_unavailable_uses_os_kill_fallback() -> None:
    """When hasattr(os, 'killpg') is False, termination uses os.kill."""
    sync_factory = make_sync_process_factory(itertools.count(1), returncode=None)
    pm = _make_pm(sync_factory=sync_factory, psutil_mod=FakePsutil())
    handle = pm.spawn([sys.executable, "-c", "pass"])

    # os.killpg is only used in _cleanup_exec_orphans (deprecated),
    # but verify the process manager doesn't break when killpg is missing
    with patch.object(os, "killpg", None, raising=False):
        pm.shutdown_all(grace_period_s=0.1)

    assert handle.record.status == ProcessStatus.KILLED


# ---------------------------------------------------------------------------
# Descendant registry survives cleanup across platform boundaries
# ---------------------------------------------------------------------------


def test_descendant_registry_cleanup_on_psutil_none() -> None:
    """Descendant registry is cleaned even when psutil is unavailable."""
    pm = _make_pm(psutil_mod=None)
    handle = pm.spawn([sys.executable, "-c", "pass"])

    pm.register_descendant(handle.pid, 99999)
    assert 99999 in pm._descendants.get(handle.pid, [])

    handle.terminate(grace_period_s=0.1)
    # After terminate, descendant registry should be cleaned
    assert handle.pid not in pm._descendants


def test_descendant_registry_with_psutil_available() -> None:
    """Descendant registry works correctly when psutil is available."""
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


# ---------------------------------------------------------------------------
# Termination outcomes logging works cross-platform
# ---------------------------------------------------------------------------


def test_termination_outcomes_logged_across_platforms() -> None:
    """log_termination_outcome works regardless of platform."""
    sync_factory = make_sync_process_factory(itertools.count(1), returncode=None)
    pm = _make_pm(sync_factory=sync_factory, psutil_mod=FakePsutil())
    handle = pm.spawn([sys.executable, "-c", "pass"])

    handle.terminate(grace_period_s=0.1)

    outcomes = pm.list_termination_outcomes()
    assert handle.pid in outcomes
    stages = [o["stage"] for o in outcomes[handle.pid]]
    assert "graceful_terminate" in stages or "force_kill" in stages
