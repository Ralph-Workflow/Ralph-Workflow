"""Single authority for parallel-worker fan-out joins.

The fan-out authority owns the parallel-worker lifecycle. It
exposes a typed :class:`FanOutDecision` enum; the reducer
and run loop read :attr:`RebaseState.last_fan_out_decision` and
never inspect the worker's :class:`WorkerState` directly.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING

from ralph.pipeline.worker_state import WorkerState, WorkerStatus

if TYPE_CHECKING:
    from ralph.pipeline.effects import Effect
    from ralph.pipeline.events import WorkerCompletedEvent, WorkerFailedEvent, WorkerStartedEvent
    from ralph.pipeline.state import PipelineState


class FanOutDecision(StrEnum):
    """The closed vocabulary the run loop reads for fan-out joins."""

    JOIN_PENDING = "join_pending"
    JOIN_COMPLETE = "join_complete"
    JOIN_PARTIAL_FAILURE = "join_partial_failure"
    JOIN_BLOCKED = "join_blocked"


def _with_fan_out_decision(state: PipelineState) -> PipelineState:
    decision = fan_out_decision(state).value
    rebase = state.rebase.model_copy(update={"last_fan_out_decision": decision})
    return state.copy_with(rebase=rebase)


def handle_fan_out_started(state: PipelineState) -> tuple[PipelineState, list[Effect]]:
    if not state.work_units or state.worker_states:
        return state, []
    new_worker_states = {
        unit.unit_id: WorkerState(unit_id=unit.unit_id, status=WorkerStatus.PENDING)
        for unit in state.work_units
    }
    new_state = state.copy_with(worker_states=new_worker_states)
    return _with_fan_out_decision(new_state), []


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
    new_state = state.copy_with(worker_states=resumed_states)
    return _with_fan_out_decision(new_state), []


def handle_worker_started(
    state: PipelineState,
    event: WorkerStartedEvent,
) -> tuple[PipelineState, list[Effect]]:
    if event.unit_id not in state.worker_states:
        return state, []
    updated = state.worker_states[event.unit_id].copy_with(
        status=WorkerStatus.RUNNING, started_at=datetime.now(UTC)
    )
    new_state = state.copy_with(worker_states={**state.worker_states, event.unit_id: updated})
    return _with_fan_out_decision(new_state), []


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
    new_state = state.copy_with(worker_states={**state.worker_states, event.unit_id: updated})
    return _with_fan_out_decision(new_state), []


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
    new_state = state.copy_with(worker_states={**state.worker_states, event.unit_id: updated})
    return _with_fan_out_decision(new_state), []


def fan_out_decision(state: PipelineState) -> FanOutDecision:
    """Return the typed :class:`FanOutDecision` for the current state.

    The function is the single authority for the question "may the
    reducer proceed past the fan-out join?". The decision is
    computed by inspecting :class:`WorkerState` instances; the
    reducer never inspects them directly.
    """
    if not state.worker_states:
        return FanOutDecision.JOIN_COMPLETE
    statuses = {worker.status for worker in state.worker_states.values()}
    if WorkerStatus.RUNNING in statuses or WorkerStatus.PENDING in statuses:
        return FanOutDecision.JOIN_PENDING
    if WorkerStatus.FAILED in statuses:
        return FanOutDecision.JOIN_PARTIAL_FAILURE
    if statuses == {WorkerStatus.SUCCEEDED}:
        return FanOutDecision.JOIN_COMPLETE
    if statuses == {WorkerStatus.CANCELLED}:
        return FanOutDecision.JOIN_BLOCKED
    return FanOutDecision.JOIN_BLOCKED


__all__ = [
    "FanOutDecision",
    "fan_out_decision",
    "handle_fan_out_started",
    "handle_worker_completed",
    "handle_worker_failed",
    "handle_worker_started",
    "handle_workers_resumed",
]
