"""Unit tests for checkpoint payload builder."""

from __future__ import annotations

from importlib import import_module

from ralph.pipeline.state import PipelineState
from ralph.policy.models import (
    BudgetCounterConfig,
    PhaseCommitPolicy,
    PhaseDefinition,
    PhaseTransition,
    PipelinePolicy,
)

checkpoint_module = import_module("ralph.checkpoint")
CheckpointBuilder = checkpoint_module.CheckpointBuilder
ExecutionHistory = checkpoint_module.ExecutionHistory
ExecutionStep = checkpoint_module.ExecutionStep
RunContext = checkpoint_module.RunContext
StepOutcome = checkpoint_module.StepOutcome


ITERATION_NUMBER = 2


def _builder_policy() -> PipelinePolicy:
    return PipelinePolicy(
        phases={
            "development": PhaseDefinition(
                drain="development",
                transitions=PhaseTransition(on_success="development_commit"),
            ),
            "development_commit": PhaseDefinition(
                drain="development_commit",
                role="commit",
                transitions=PhaseTransition(on_success="complete"),
                commit_policy=PhaseCommitPolicy(
                    increments_counter="iteration",
                    loop_resets=[],
                ),
            ),
            "complete": PhaseDefinition(
                drain="complete",
                role="terminal",
                terminal_outcome="success",
                transitions=PhaseTransition(on_success="complete"),
            ),
        },
        budget_counters={
            "iteration": BudgetCounterConfig(
                tracks_budget=True, description="iteration counter", default_max=5
            ),
        },
        entry_phase="development",
        terminal_phase="complete",
    )


def test_checkpoint_builder_builds_payload_from_state() -> None:
    """Builder should produce a payload with state, context, and history."""
    policy = _builder_policy()
    state = PipelineState(
        phase="development",
        outer_progress={"iteration": ITERATION_NUMBER, "reviewer_pass": 1},
    )
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
        .pipeline_policy(policy)
        .build()
    )

    assert payload.phase == "development"
    assert payload.state.get_outer_progress("iteration") == ITERATION_NUMBER
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


def test_checkpoint_builder_without_policy_zeroes_progress_mirrors() -> None:
    """Without pipeline_policy, stale non-zero progress values must be reset to 0.

    Pins the no-policy branch in derive_run_context_progress: a RunContext carrying
    non-zero actual_developer_runs / actual_reviewer_runs must not leak into the
    built payload when no policy is provided.
    """
    state = PipelineState(phase="planning")
    stale_context = RunContext(
        run_id="test-run",
        actual_developer_runs=5,
        actual_reviewer_runs=3,
    )

    payload = CheckpointBuilder.new().state(state).run_context(stale_context).build()

    assert payload.run_context.actual_developer_runs == 0
    assert payload.run_context.actual_reviewer_runs == 0
