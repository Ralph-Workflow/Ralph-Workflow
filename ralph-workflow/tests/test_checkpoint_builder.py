"""Unit tests for checkpoint payload builder."""

from __future__ import annotations

from importlib import import_module

from ralph.pipeline.state import PipelineState

checkpoint_module = import_module("ralph.checkpoint")
CheckpointBuilder = checkpoint_module.CheckpointBuilder
ExecutionHistory = checkpoint_module.ExecutionHistory
ExecutionStep = checkpoint_module.ExecutionStep
RunContext = checkpoint_module.RunContext
StepOutcome = checkpoint_module.StepOutcome


ITERATION_NUMBER = 2


def test_checkpoint_builder_builds_payload_from_state() -> None:
    """Builder should produce a payload with state, context, and history."""
    state = PipelineState(phase="development", iteration=ITERATION_NUMBER, reviewer_pass=1)
    context = RunContext.new().record_developer_iteration()
    history = ExecutionHistory.new().add_step_bounded(
        ExecutionStep.new("development", ITERATION_NUMBER, "agent_run", StepOutcome.success()),
        limit=5,
    )

    payload = (
        CheckpointBuilder.new()
        .state(state)
        .run_context(context)
        .execution_history(history)
        .working_dir("/tmp/repo")
        .build()
    )

    assert payload.phase == "development"
    assert payload.iteration == ITERATION_NUMBER
    assert payload.working_dir == "/tmp/repo"
    assert payload.run_context.actual_developer_runs == ITERATION_NUMBER
    assert len(payload.execution_history.steps) == 1


def test_checkpoint_builder_requires_pipeline_state() -> None:
    """Builder should reject incomplete payloads."""
    try:
        CheckpointBuilder.new().build()
    except ValueError as exc:
        assert "state" in str(exc).lower()
    else:
        raise AssertionError("expected ValueError when state is missing")


def test_checkpoint_payload_round_trips_to_dict() -> None:
    """Built payloads should serialize into JSON-safe dictionaries."""
    payload = CheckpointBuilder.new().state(PipelineState(phase="planning")).build()

    data = payload.to_dict()

    assert data["state"]["phase"] == "planning"
    assert data["run_context"]["resume_count"] == 0
    assert data["execution_history"]["steps"] == []
