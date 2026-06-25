"""Black-box tests for ``ManagedAsyncProcess`` async context manager.

wt-024 memory-perf GAP-PROC-01: ``ManagedAsyncProcess`` lacks an
async context-manager protocol (``__aenter__``/``__aexit__``). Its
sync sibling ``ManagedProcess`` provides ``__enter__``/``__exit__``,
so the absence on the async side means any future async caller that
forgets a ``try/finally`` leaks the async subprocess.

Because ``ManagedAsyncProcess.terminate`` is ``async def``, the
correct pattern is the ASYNC context-manager protocol — a sync
``__exit__`` calling ``self.terminate()`` would return an un-awaited
coroutine and never terminate.

These tests are self-contained: they inline ``ProcessManager`` +
``FakePsutil`` + ``make_async_process_factory`` and assert on
``record.status`` (the real termination outcome via
``manager._escalate_termination_async``), NOT on
``FakeAsyncProcess.terminate`` (which ``ManagedAsyncProcess.terminate``
never calls — it routes through the manager).
"""

from __future__ import annotations

import itertools
import sys

from ralph.process.manager import ProcessManager, ProcessManagerPolicy
from ralph.process.manager._process_status import (
    _TERMINAL_STATUSES,
    ProcessStatus,
)
from ralph.testing.fake_process import (
    FakePsutil,
    make_async_process_factory,
)

_FAST_POLICY = ProcessManagerPolicy(
    default_grace_period_s=0.3,
    kill_followup_timeout_s=0.5,
    log_events=False,
    enable_zombie_reaper=False,
)


async def test_async_context_manager_terminates_on_exit() -> None:
    """``async with handle`` must terminate the process on exit when still non-terminal."""
    async_factory = make_async_process_factory(itertools.count(1), returncode=None)
    pm = ProcessManager(
        policy=_FAST_POLICY,
        async_process_factory=async_factory,
        psutil=FakePsutil(),
    )
    handle = await pm.spawn_async([sys.executable, "-c", "pass"])
    assert handle.record.status == ProcessStatus.RUNNING

    async with handle:
        pass  # exit the block without explicit terminate

    assert handle.record.status in _TERMINAL_STATUSES, (
        f"async-with exit should have terminated the process; status={handle.record.status}"
    )


async def test_async_context_manager_noop_when_already_terminal() -> None:
    """``async with handle`` on an already-terminal handle must be a no-op."""
    async_factory = make_async_process_factory(itertools.count(1), returncode=None)
    pm = ProcessManager(
        policy=_FAST_POLICY,
        async_process_factory=async_factory,
        psutil=FakePsutil(),
    )
    handle = await pm.spawn_async([sys.executable, "-c", "pass"])

    # Mark the handle as already terminated via the sync manager API
    pm._mark_exited(handle.record, returncode=0)
    assert handle.record.status == ProcessStatus.EXITED

    async with handle:
        pass  # no raise, no re-escalation

    assert handle.record.status == ProcessStatus.EXITED


async def test_async_context_manager_returns_self() -> None:
    """``async with handle as x`` must return the handle itself."""
    async_factory = make_async_process_factory(itertools.count(1), returncode=None)
    pm = ProcessManager(
        policy=_FAST_POLICY,
        async_process_factory=async_factory,
        psutil=FakePsutil(),
    )
    handle = await pm.spawn_async([sys.executable, "-c", "pass"])

    async with handle as bound:
        assert bound is handle

    assert handle.record.status in _TERMINAL_STATUSES
