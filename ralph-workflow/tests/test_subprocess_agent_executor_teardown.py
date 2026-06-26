"""Regression tests for SubprocessAgentExecutor teardown invariants.

These tests verify the AC-02 finally-block guarantee: a non-terminal agent
handle is always terminated when ``SubprocessAgentExecutor.run()`` exits,
even when the underlying asyncio.gather() is cancelled mid-flight or the
subprocess's ``handle.wait()`` would otherwise hang forever.

The teardown guarantee is the foundation for the
``# mcp-timeout-ok: bounded by activity-aware idle watchdog teardown``
marker on ``await asyncio.gather(drain_output(), handle.wait())`` in
``ralph/agents/subprocess_executor.py``: the surrounding finally block
ALWAYS terminates a non-terminal handle, so a hard wait_for ceiling
around the gather is unnecessary (and would risk killing a slow-but-
healthy agent).

This file intentionally does NOT carry ``pytestmark = pytest.mark.subprocess_e2e``
because the test harness excludes subprocess_e2e-marked tests from the
canonical ``make verify`` run (see ``ralph/test_suites.py``). The tests
use only fakes (FakeControllableAsyncProcess + an injected ProcessManager
factory) and execute in well under 1.0s each.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import TYPE_CHECKING

import pytest

from ralph.agents.subprocess_executor import SubprocessAgentExecutor
from ralph.pipeline.work_unit import WorkUnit
from ralph.process.manager import ProcessManager, ProcessManagerPolicy
from ralph.testing._fake_psutil import FakePsutil
from ralph.testing._fake_psutil_process import FakePsutilProcess
from ralph.testing.fake_process import FakeControllableAsyncProcess

if TYPE_CHECKING:
    from collections.abc import Callable

    from ralph.pipeline.worker_state import WorkerStatus


def _ignore_output(_line: str) -> None:
    return None


def _ignore_status(_status: WorkerStatus) -> None:
    return None


def _make_unit(unit_id: str) -> WorkUnit:
    return WorkUnit(unit_id=unit_id, description=f"unit {unit_id}")


class _RecordingControllableAsyncProcess(FakeControllableAsyncProcess):
    """FakeControllableAsyncProcess that records terminate() / kill() calls.

    The base fake silently flips internal state when terminate() / kill()
    are called. This subclass records each call so a regression test can
    assert the teardown guarantee (the finally block always terminates a
    non-terminal handle) without relying on the FakeControllableAsyncProcess
    internal _returncode field, which is a private attribute and would
    violate the black-box testability rule.

    The constructor uses an explicit signature matching the parent class
    so the ``super().__init__(...)`` call is fully typed (the type-ignore
    policy forbids suppressions in test files).
    """

    def __init__(
        self,
        pid: int,
        *,
        stdout_data: bytes = b"",
        returncode: int = 0,
        completion_event: asyncio.Event | None = None,
    ) -> None:
        super().__init__(
            pid=pid,
            stdout_data=stdout_data,
            returncode=returncode,
            completion_event=completion_event,
        )
        self.terminate_calls: int = 0
        self.kill_calls: int = 0

    def terminate(self) -> None:
        self.terminate_calls += 1
        super().terminate()

    def kill(self) -> None:
        self.kill_calls += 1
        super().kill()


class _RecordingPsutilProcess(FakePsutilProcess):
    """FakePsutilProcess that records terminate() calls.

    When psutil is injected, ProcessManager's ``_escalate_termination_async``
    uses the psutil process-tree path: ``root = psutil.process_from_pid(pid)``
    then ``root.terminate()`` (NOT the underlying async fake's terminate()).
    Subclassing FakePsutilProcess and overriding terminate() keeps the test
    fully typed (the type-ignore policy forbids suppressions in test files).
    """

    def __init__(self, pid: int) -> None:
        super().__init__(pid=pid)
        self.terminate_calls: int = 0

    def terminate(self) -> None:
        self.terminate_calls += 1
        super().terminate()


def _build_factory(
    fake: _RecordingControllableAsyncProcess,
) -> Callable[..., asyncio.Future[object]]:
    """Return an async factory that hands out the recording fake.

    The signature matches ``_AsyncProcessFactory`` (Protocol) but is annotated
    loosely enough that the test file stays fully typed without suppressions.
    """

    async def _factory(
        command: list[str],
        *,
        cwd: str | None,
        env: dict[str, str] | None,
        stdin: int | None,
        stdout: int | None,
        stderr: int | None,
        start_new_session: bool,
    ) -> object:
        del command, cwd, env, stdin, stdout, stderr, start_new_session
        return fake

    return _factory


def _make_fake_psutil(pid: int) -> tuple[FakePsutil, _RecordingPsutilProcess]:
    """Construct a FakePsutil with ``pid`` pre-registered as a recording root.

    Injecting a FakePsutil whose ``process_from_pid(pid)`` returns a
    ``FakePsutilProcess`` makes the manager's pre-kill liveness check
    return ``ALIVE`` for our synthetic PID (otherwise the manager
    short-circuits ``terminate()`` with ``cause=already_gone`` because
    ``os.kill(pid, 0)`` raises ``ProcessLookupError`` on a fake PID).
    Pre-registering a ``_RecordingPsutilProcess`` lets the test assert
    the psutil-side ``terminate()`` calls.
    """
    fake_psutil = FakePsutil()
    recording = _RecordingPsutilProcess(pid=pid)
    fake_psutil._processes[pid] = recording
    return fake_psutil, recording


@pytest.mark.asyncio
async def test_finally_block_terminates_non_terminal_handle() -> None:
    """AC-02 regression: the finally block terminates a non-terminal handle.

    Configures a recording fake whose ``completion_event`` is NEVER set, so
    ``handle.wait()`` would hang forever absent the finally teardown.
    Wraps ``executor.run()`` in ``asyncio.wait_for(...)`` with a short
    timeout so the hanging gather is cancelled and the finally block is
    forced to run.

    Asserts the recording psutil root observed at least one ``terminate()``
    call — the teardown guarantee that backs the
    ``# mcp-timeout-ok: bounded by activity-aware idle watchdog teardown``
    marker on ``await asyncio.gather(drain_output(), handle.wait())``.

    The test is purely black-box: it observes the INJECTED fakes only,
    never reads production private attributes, and uses no real subprocess
    / sleep.
    """
    pid = 42
    fake = _RecordingControllableAsyncProcess(
        pid=pid,
        stdout_data=b"ready\n",
        completion_event=asyncio.Event(),
    )

    fake_psutil, recording_psutil = _make_fake_psutil(pid)
    pm = ProcessManager(
        policy=ProcessManagerPolicy(
            default_grace_period_s=0.0,
            kill_followup_timeout_s=0.0,
            log_events=False,
            enable_zombie_reaper=False,
        ),
        async_process_factory=_build_factory(fake),
        psutil=fake_psutil,
    )

    executor = SubprocessAgentExecutor(["fake-cmd"], _pm=pm)
    unit = _make_unit("teardown-invariant-test")

    with suppress(asyncio.TimeoutError, asyncio.CancelledError):
        await asyncio.wait_for(
            executor.run(
                unit,
                on_output=_ignore_output,
                on_status=_ignore_status,
            ),
            timeout=0.5,
        )

    assert recording_psutil.terminate_calls >= 1, (
        f"expected the teardown invariant to call terminate() on the fake "
        f"handle's psutil root at least once (the finally block must always "
        f"terminate a non-terminal handle); got "
        f"terminate_calls={recording_psutil.terminate_calls}"
    )


@pytest.mark.asyncio
async def test_normal_completion_does_not_terminate_via_finally() -> None:
    """When the gather completes normally, the finally block is a no-op for termination.

    Counterpart to ``test_finally_block_terminates_non_terminal_handle``:
    if the agent subprocess exits cleanly, the handle's record transitions
    to a terminal status BEFORE the finally block runs, so the
    ``if handle.record.status not in _TERMINAL_STATUSES`` guard skips the
    redundant ``handle.terminate(...)`` call.

    This pins the contract that the finally block is a SAFETY NET, not a
    mandatory kill: it only fires when the handle is still alive (e.g. the
    gather was cancelled or the run raised before completion).
    """
    pid = 43
    completion = asyncio.Event()
    fake = _RecordingControllableAsyncProcess(
        pid=pid,
        stdout_data=b"ready\n",
        completion_event=completion,
    )

    fake_psutil, recording_psutil = _make_fake_psutil(pid)
    pm = ProcessManager(
        policy=ProcessManagerPolicy(
            default_grace_period_s=0.0,
            kill_followup_timeout_s=0.0,
            log_events=False,
            enable_zombie_reaper=False,
        ),
        async_process_factory=_build_factory(fake),
        psutil=fake_psutil,
    )

    async def release_after_drain() -> None:
        await asyncio.sleep(0)
        completion.set()

    on_drain_started = asyncio.Event()

    def on_output(line: str) -> None:
        if "ready" in line:
            on_drain_started.set()

    executor = SubprocessAgentExecutor(["fake-cmd"], _pm=pm)
    unit = _make_unit("normal-completion-test")

    release_task = asyncio.create_task(release_after_drain())
    try:
        await asyncio.wait_for(
            executor.run(
                unit,
                on_output=on_output,
                on_status=_ignore_status,
            ),
            timeout=1.0,
        )
        assert on_drain_started.is_set(), "drain_output never observed the pre-fed output"
    finally:
        if not release_task.done():
            release_task.cancel()
            with suppress(asyncio.CancelledError):
                await release_task

    assert recording_psutil.terminate_calls == 0, (
        f"normal completion should not invoke the finally-block terminate; "
        f"got terminate_calls={recording_psutil.terminate_calls} (the finally "
        f"guard is expected to skip termination when the handle is already "
        f"terminal)"
    )


__all__ = ["test_finally_block_terminates_non_terminal_handle"]
