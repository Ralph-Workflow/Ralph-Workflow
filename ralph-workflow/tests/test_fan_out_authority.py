"""Unit tests for the fan-out authority (Authority 6)."""

from __future__ import annotations

from ralph.pipeline._fan_out_authority import (
    FanOutDecision,
    fan_out_decision,
)
from ralph.pipeline.state import PipelineState
from ralph.pipeline.worker_state import WorkerState, WorkerStatus


def test_fan_out_decision_enum_has_four_values() -> None:
    assert len(FanOutDecision) == 4


def test_fan_out_decision_join_complete_when_no_workers() -> None:
    state = PipelineState(phase="plan")
    assert fan_out_decision(state) is FanOutDecision.JOIN_COMPLETE


def test_fan_out_decision_join_pending_when_workers_running() -> None:
    state = PipelineState(
        phase="plan",
        worker_states={
            "u1": WorkerState(unit_id="u1", status=WorkerStatus.RUNNING),
        },
    )
    assert fan_out_decision(state) is FanOutDecision.JOIN_PENDING


def test_fan_out_decision_partial_failure_when_any_failed() -> None:
    state = PipelineState(
        phase="plan",
        worker_states={
            "u1": WorkerState(unit_id="u1", status=WorkerStatus.SUCCEEDED),
            "u2": WorkerState(unit_id="u2", status=WorkerStatus.FAILED),
        },
    )
    assert fan_out_decision(state) is FanOutDecision.JOIN_PARTIAL_FAILURE


def test_fan_out_decision_complete_when_all_succeeded() -> None:
    state = PipelineState(
        phase="plan",
        worker_states={
            "u1": WorkerState(unit_id="u1", status=WorkerStatus.SUCCEEDED),
            "u2": WorkerState(unit_id="u2", status=WorkerStatus.SUCCEEDED),
        },
    )
    assert fan_out_decision(state) is FanOutDecision.JOIN_COMPLETE


def test_fan_out_decision_blocked_when_all_cancelled() -> None:
    state = PipelineState(
        phase="plan",
        worker_states={
            "u1": WorkerState(unit_id="u1", status=WorkerStatus.CANCELLED),
        },
    )
    assert fan_out_decision(state) is FanOutDecision.JOIN_BLOCKED
