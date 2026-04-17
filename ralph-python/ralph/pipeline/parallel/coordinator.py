"""Structured concurrency coordinator for parallel development fan-out."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from ralph.pipeline.events import (
    Event,
    PipelineEvent,
    WorkerCompletedEvent,
    WorkerFailedEvent,
    WorkerStartedEvent,
)
from ralph.pipeline.parallel.scheduler import schedule_next_wave
from ralph.pipeline.worker_state import WorkerStatus

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.agents.executor import AgentExecutor, WorkerResult
    from ralph.display.parallel_display import ParallelDisplay
    from ralph.pipeline.effects import FanOutDevelopmentEffect
    from ralph.pipeline.state import PipelineState
    from ralph.pipeline.work_units import WorkUnit


class _WorkerFailureError(Exception):
    def __init__(self, unit_id: str, exit_code: int, error: str) -> None:
        super().__init__(error)
        self.unit_id = unit_id
        self.exit_code = exit_code
        self.error = error


def _flatten_worker_failures(
    exceptions: tuple[BaseException, ...],
) -> tuple[list[_WorkerFailureError], list[Exception]]:
    failures: list[_WorkerFailureError] = []
    unexpected: list[Exception] = []
    stack = list(exceptions)

    while stack:
        current = stack.pop()
        if isinstance(current, BaseExceptionGroup):
            stack.extend(current.exceptions)
            continue
        if isinstance(current, _WorkerFailureError):
            failures.append(current)
            continue
        if isinstance(current, Exception):
            unexpected.append(current)

    return failures, unexpected


def _nonzero_exit_error(result: WorkerResult) -> str:
    if result.final_message:
        return result.final_message
    return f"Worker {result.unit_id} exited with code {result.exit_code}"


async def _run_worker(
    unit: WorkUnit,
    executor: AgentExecutor,
    display: ParallelDisplay,
    completion_queue: asyncio.Queue[WorkerResult],
) -> None:
    def on_output(line: str) -> None:
        display.emit(unit.unit_id, line)

    def on_status(status: WorkerStatus) -> None:
        display.set_status(unit.unit_id, status)

    try:
        result = await executor.run(unit, on_output=on_output, on_status=on_status)
    except asyncio.CancelledError:
        display.set_status(unit.unit_id, WorkerStatus.CANCELLED)
        raise
    except BaseException as exc:
        if isinstance(exc, _WorkerFailureError):
            raise
        if isinstance(exc, Exception):
            display.set_status(unit.unit_id, WorkerStatus.FAILED)
            raise _WorkerFailureError(unit.unit_id, 1, str(exc)) from exc
        raise

    if result.exit_code != 0:
        raise _WorkerFailureError(
            unit_id=unit.unit_id,
            exit_code=result.exit_code,
            error=_nonzero_exit_error(result),
        )

    await completion_queue.put(result)


async def run_fan_out(
    effect: FanOutDevelopmentEffect,
    executor: AgentExecutor,
    display: ParallelDisplay,
    checkpoint_path: Path,
    state: PipelineState,
) -> list[Event]:
    """Execute parallel work units while respecting DAG dependencies and worker caps."""
    del checkpoint_path, state

    events: list[Event] = [PipelineEvent.FAN_OUT_STARTED]
    if not effect.work_units:
        return [*events, PipelineEvent.ALL_WORKERS_COMPLETE]

    pending = {unit.unit_id for unit in effect.work_units}
    completed: set[str] = set()
    running: dict[str, WorkUnit] = {}
    completion_queue: asyncio.Queue[WorkerResult] = asyncio.Queue()

    try:
        async with asyncio.TaskGroup() as task_group:
            while pending or running:
                ready = schedule_next_wave(
                    completed,
                    effect.work_units,
                    set(running),
                    effect.max_workers,
                )

                for unit in ready:
                    pending.discard(unit.unit_id)
                    running[unit.unit_id] = unit
                    events.append(WorkerStartedEvent(unit_id=unit.unit_id))
                    task_group.create_task(
                        _run_worker(unit, executor, display, completion_queue),
                        name=unit.unit_id,
                    )

                if running:
                    result = await completion_queue.get()
                    running.pop(result.unit_id, None)
                    completed.add(result.unit_id)
                    events.append(
                        WorkerCompletedEvent(
                            unit_id=result.unit_id,
                            exit_code=result.exit_code,
                            commit_sha="",
                        )
                    )
                    continue

                if pending:
                    break
    except* Exception as group:
        failures, unexpected = _flatten_worker_failures(group.exceptions)
        seen_failures = {event.unit_id for event in events if isinstance(event, WorkerFailedEvent)}
        for failure in failures:
            if failure.unit_id in seen_failures:
                continue
            running.pop(failure.unit_id, None)
            events.append(
                WorkerFailedEvent(
                    unit_id=failure.unit_id,
                    exit_code=failure.exit_code,
                    error=failure.error,
                )
            )
            seen_failures.add(failure.unit_id)
        if unexpected:
            raise ExceptionGroup("Unexpected fan-out coordinator failure", unexpected) from None
    else:
        events.append(PipelineEvent.ALL_WORKERS_COMPLETE)

    return events


__all__ = ["run_fan_out"]
