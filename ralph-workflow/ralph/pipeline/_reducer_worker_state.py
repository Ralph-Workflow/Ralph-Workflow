"""Worker state transition handlers for the pipeline reducer.

These pure functions update WorkerState entries in PipelineState when
individual worker lifecycle events arrive.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from ralph.pipeline.worker_state import WorkerState, WorkerStatus

if TYPE_CHECKING:
    from ralph.pipeline.effects import Effect
    from ralph.pipeline.events import WorkerCompletedEvent, WorkerFailedEvent, WorkerStartedEvent
    from ralph.pipeline.state import PipelineState


def handle_fan_out_started(state: PipelineState) -> tuple[PipelineState, list[Effect]]:
    if not state.work_units or state.worker_states:
        return state, []
    new_worker_states = {
        unit.unit_id: WorkerState(unit_id=unit.unit_id, status=WorkerStatus.PENDING)
        for unit in state.work_units
    }
    return state.copy_with(worker_states=new_worker_states), []


def handle_workers_resumed(state: PipelineState) -> tuple[PipelineState, list[Effect]]:
    if not state.worker_states:
        return state, []
    resumed_states = {
        unit_id: (
            worker_state.copy_with(status=WorkerStatus.PENDING)
            if worker_state.status == WorkerStatus.RUNNING
            else worker_state
        )
        for unit_id, worker_state in state.worker_states.items()
    }
    return state.copy_with(worker_states=resumed_states), []


def handle_worker_started(
    state: PipelineState,
    event: WorkerStartedEvent,
) -> tuple[PipelineState, list[Effect]]:
    if event.unit_id not in state.worker_states:
        return state, []
    updated = state.worker_states[event.unit_id].copy_with(
        status=WorkerStatus.RUNNING, started_at=datetime.now(UTC)
    )
    return state.copy_with(worker_states={**state.worker_states, event.unit_id: updated}), []


def handle_worker_completed(
    state: PipelineState,
    event: WorkerCompletedEvent,
) -> tuple[PipelineState, list[Effect]]:
    if event.unit_id not in state.worker_states:
        return state, []
    updated = state.worker_states[event.unit_id].copy_with(
        status=WorkerStatus.SUCCEEDED,
        exit_code=event.exit_code,
        finished_at=datetime.now(UTC),
    )
    return state.copy_with(worker_states={**state.worker_states, event.unit_id: updated}), []


def handle_worker_failed(
    state: PipelineState,
    event: WorkerFailedEvent,
) -> tuple[PipelineState, list[Effect]]:
    if event.unit_id not in state.worker_states:
        return state, []
    updated = state.worker_states[event.unit_id].copy_with(
        status=WorkerStatus.FAILED,
        exit_code=event.exit_code,
        error_message=event.error,
        finished_at=datetime.now(UTC),
    )
    return state.copy_with(worker_states={**state.worker_states, event.unit_id: updated}), []
