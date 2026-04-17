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


def _blocked_dependency_error(unit: WorkUnit, failed_unit_ids: set[str]) -> str | None:
    blocked_by = sorted(dep for dep in unit.dependencies if dep in failed_unit_ids)
    if not blocked_by:
        return None
    return f"Blocked by failed dependencies: {', '.join(blocked_by)}"


def _blocked_pending_failures(
    work_units: tuple[WorkUnit, ...],
    pending_unit_ids: set[str],
    failed_unit_ids: set[str],
) -> list[WorkerFailedEvent]:
    pending_units = {unit.unit_id: unit for unit in work_units if unit.unit_id in pending_unit_ids}
    blocked_events: list[WorkerFailedEvent] = []
    expanded_failures = set(failed_unit_ids)

    while True:
        progress_made = False
        for unit_id, unit in list(pending_units.items()):
            blocked_error = _blocked_dependency_error(unit, expanded_failures)
            if blocked_error is None:
                continue
            blocked_events.append(
                WorkerFailedEvent(unit_id=unit_id, exit_code=1, error=blocked_error)
            )
            expanded_failures.add(unit_id)
            del pending_units[unit_id]
            progress_made = True
        if not progress_made:
            return blocked_events


def _append_terminal_failure_events(
    *,
    events: list[Event],
    work_units: tuple[WorkUnit, ...],
    pending: set[str],
    running: dict[str, WorkUnit],
    failures: list[_WorkerFailureError],
) -> None:
    seen_failures = {event.unit_id for event in events if isinstance(event, WorkerFailedEvent)}
    failed_unit_ids = {failure.unit_id for failure in failures}

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

    for unit_id in list(running):
        if unit_id in seen_failures:
            continue
        events.append(
            WorkerFailedEvent(
                unit_id=unit_id,
                exit_code=1,
                error="Cancelled because another worker failed",
            )
        )
        seen_failures.add(unit_id)

    blocked_events = _blocked_pending_failures(
        work_units,
        pending,
        failed_unit_ids | seen_failures,
    )
    for blocked_event in blocked_events:
        if blocked_event.unit_id in seen_failures:
            continue
        events.append(blocked_event)
        seen_failures.add(blocked_event.unit_id)


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
        _append_terminal_failure_events(
            events=events,
            work_units=effect.work_units,
            pending=pending,
            running=running,
            failures=failures,
        )
        if unexpected:
            raise ExceptionGroup("Unexpected fan-out coordinator failure", unexpected) from None
    else:
        events.append(PipelineEvent.ALL_WORKERS_COMPLETE)

    return events


__all__ = ["run_fan_out"]
