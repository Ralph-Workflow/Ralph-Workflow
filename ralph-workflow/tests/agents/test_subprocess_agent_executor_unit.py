"""Black-box unit tests for SubprocessAgentExecutor.

Tests verify that SubprocessAgentExecutor:
- spawns processes with correct label via ProcessManager
- tracks processes to terminal state after normal completion
- marks processes KILLED when cancelled

No real subprocesses are spawned; all tests use FakeControllableAsyncProcess
and FakePsutil injected via a custom ProcessManager.
"""

from __future__ import annotations

import asyncio
import contextlib
import itertools
import sys

import pytest

from ralph.agents.subprocess_executor import (
    SubprocessAgentExecutor,
    agent_process_label,
)
from ralph.pipeline.work_units import WorkUnit
from ralph.process import ProcessManager, ProcessManagerPolicy, ProcessStatus
from ralph.testing.fake_process import FakeControllableAsyncProcess, FakePsutil

# --------------------------------------------------------------------------/
# CAT-AGENT-UNIT: normal completion + label contract
# --------------------------------------------------------------------------/


@pytest.mark.asyncio
async def test_subprocess_executor_tracks_process_with_correct_label() -> None:
    """SubprocessAgentExecutor.run() spawns with agent_process_label and tracks."""

    # Instant completion event
    event = asyncio.Event()
    event.set()

    pid_counter = itertools.count(1)

    async def async_factory(
        command: tuple[str, ...],
        *,
        cwd: str | None,
        env: dict[str, str] | None,
        stdin: int | None,
        stdout: int | None,
        stderr: int | None,
        start_new_session: bool,
    ) -> FakeControllableAsyncProcess:
        return FakeControllableAsyncProcess(
            pid=next(pid_counter),
            completion_event=event,
        )

    pm = ProcessManager(
        async_process_factory=async_factory,
        psutil=FakePsutil(),
        policy=ProcessManagerPolicy(
            default_grace_period_s=0.3,
            kill_followup_timeout_s=0.5,
            log_events=False,
        ),
    )

    executor = SubprocessAgentExecutor(
        command=[sys.executable, "-c", "pass"],
        _pm=pm,
    )

    unit = WorkUnit(unit_id="unit-1", description="test")

    await executor.run(
        unit,
        on_output=lambda _: None,
        on_status=lambda _: None,
    )

    # Verify exactly one terminal record
    records = pm.list_records(include_active=False, include_terminal=True)
    assert len(records) == 1, f"Expected 1 terminal record, got {len(records)}"

    record = records[0]
    expected_label = agent_process_label("unit-1", None)
    assert record.label == expected_label, (
        f"Expected label '{expected_label}', got '{record.label}'"
    )
    assert record.status in (ProcessStatus.EXITED, ProcessStatus.KILLED)


# --------------------------------------------------------------------------/
# CAT-AGENT-CANCEL: cancellation/shutdown path
# --------------------------------------------------------------------------/


@pytest.mark.asyncio
async def test_subprocess_executor_cancellation_marks_process_killed() -> None:
    """Cancelling an in-flight executor.run() marks process KILLED."""

    # Process stays alive indefinitely (event not set)
    event = asyncio.Event()

    pid_counter = itertools.count(100)

    async def async_factory(
        command: tuple[str, ...],
        *,
        cwd: str | None,
        env: dict[str, str] | None,
        stdin: int | None,
        stdout: int | None,
        stderr: int | None,
        start_new_session: bool,
    ) -> FakeControllableAsyncProcess:
        return FakeControllableAsyncProcess(
            pid=next(pid_counter),
            completion_event=event,
        )

    # Use FakePsutil so cancellation routes through _escalate_termination_async's
    # psutil branch (_do_terminate in run_in_executor). FakePsutil.process_from_pid
    # raises NoSuchProcess for unregistered pids, so _do_terminate returns False
    # (still_alive=False) and _mark_killed is called cleanly without ProcessTerminationError.
    pm = ProcessManager(
        async_process_factory=async_factory,
        psutil=FakePsutil(),
        policy=ProcessManagerPolicy(
            default_grace_period_s=0.0,
            kill_followup_timeout_s=0.0,
            log_events=False,
        ),
    )

    executor = SubprocessAgentExecutor(
        command=[sys.executable, "-c", "pass"],
        _pm=pm,
    )

    unit = WorkUnit(unit_id="unit-cancel-1", description="test")

    task = asyncio.create_task(
        executor.run(
            unit,
            on_output=lambda _: None,
            on_status=lambda _: None,
        )
    )

    # Yield twice to allow spawn_async() to complete and task to block at asyncio.gather
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert len(pm.list_active()) == 1, (
        f"Expected 1 active process before cancel, got {len(pm.list_active())}"
    )

    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task

    # Verify exactly one terminal record with KILLED status
    records = pm.list_records(include_active=False, include_terminal=True)
    assert len(records) == 1, f"Expected 1 terminal record, got {len(records)}"
    assert records[0].status == ProcessStatus.KILLED

    # Verify list_active is empty
    assert len(pm.list_active()) == 0
