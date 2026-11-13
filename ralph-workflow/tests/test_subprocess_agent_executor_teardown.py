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

_CANCELLATION_YIELD_BUDGET = 100

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
    stdin = None

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

    def terminate(self, **_kwargs: object) -> None:
        self.terminate_calls += 1
        super().terminate()

    def kill(self, **_kwargs: object) -> None:
        self.kill_calls += 1
        super().kill()


class _RecordingPsutilProcess(FakePsutilProcess):
    def __init__(self, pid: int) -> None:
        super().__init__(pid=pid)
        self.terminate_calls: int = 0

    def terminate(self, **_kwargs: object) -> None:
        self.terminate_calls += 1
        super().terminate()


def _build_factory(
    fake: _RecordingControllableAsyncProcess,
) -> Callable[..., asyncio.Future[object]]:

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
    fake_psutil = FakePsutil()
    recording = _RecordingPsutilProcess(pid=pid)
    fake_psutil._processes[pid] = recording
    return fake_psutil, recording


@pytest.mark.asyncio
async def test_finally_block_terminates_non_terminal_handle() -> None:
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

    run_task = asyncio.ensure_future(
        executor.run(
            unit,
            on_output=_ignore_output,
            on_status=_ignore_status,
        )
    )
    for _ in range(_CANCELLATION_YIELD_BUDGET):
        if fake.stdout.at_eof():
            break
        await asyncio.sleep(0)
    run_task.cancel()
    with suppress(asyncio.TimeoutError, asyncio.CancelledError):
        await run_task

    assert recording_psutil.terminate_calls >= 1, (
        f"expected the teardown invariant to call terminate() on the fake "
        f"handle's psutil root at least once (the finally block must always "
        f"terminate a non-terminal handle); got "
        f"terminate_calls={recording_psutil.terminate_calls}"
    )


@pytest.mark.asyncio
async def test_normal_completion_does_not_terminate_via_finally() -> None:
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


@pytest.mark.asyncio
async def test_silent_then_successful_child_keeps_output_and_is_not_terminated() -> None:
    from ralph.agents.execution_state import AgentExecutionState
    from ralph.agents.idle_watchdog import IdleWatchdog, TimeoutPolicy, WatchdogVerdict
    from ralph.agents.timeout_clock import FakeClock

    policy = TimeoutPolicy(
        idle_timeout_seconds=0.05,
        max_waiting_on_child_seconds=10.0,
        idle_poll_interval_seconds=0.001,
        drain_window_seconds=30.0,
        suspect_waiting_on_child_seconds=None,
        max_waiting_on_child_no_progress_seconds=None,
        stuck_job_sub_ceiling_seconds=None,
        os_descendant_only_ceiling_seconds=None,
    )
    clock = FakeClock(start=0.0)
    watchdog = IdleWatchdog(policy, clock)
    watchdog.record_activity()
    clock.advance(20.0)
    verdict = watchdog.evaluate(classify_quiet=lambda: AgentExecutionState.ACTIVE)
    assert verdict != WatchdogVerdict.FIRE

    pid = 44
    completion = asyncio.Event()
    fake = _RecordingControllableAsyncProcess(
        pid=pid,
        stdout_data=b"line-one\nline-two\n",
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

    observed: list[str] = []

    async def release_after_drain() -> None:
        for _ in range(20):
            await asyncio.sleep(0)
        completion.set()

    executor = SubprocessAgentExecutor(["fake-cmd"], _pm=pm)
    unit = _make_unit("silent-then-success")
    release_task = asyncio.create_task(release_after_drain())
    try:
        await asyncio.wait_for(
            executor.run(
                unit,
                on_output=observed.append,
                on_status=_ignore_status,
            ),
            timeout=1.0,
        )
    finally:
        if not release_task.done():
            release_task.cancel()
            with suppress(asyncio.CancelledError):
                await release_task

    assert observed == ["line-one", "line-two"], (
        f"silent-then-successful child lost output; got {observed!r}"
    )
    assert recording_psutil.terminate_calls == 0, (
        f"silent-then-successful child must not be terminated; "
        f"got terminate_calls={recording_psutil.terminate_calls}"
    )


__all__ = [
    "test_finally_block_terminates_non_terminal_handle",
    "test_silent_then_successful_child_keeps_output_and_is_not_terminated",
]
