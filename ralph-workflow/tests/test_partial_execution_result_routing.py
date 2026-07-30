"""Black-box routing tests for partial execution results."""

from __future__ import annotations

from pathlib import Path

import pytest

from ralph.pipeline.events import Event, ExecutionResultEvent, PipelineEvent
from ralph.pipeline.reducer import reduce as reducer_reduce
from ralph.pipeline.state import PipelineState
from ralph.policy.loader import load_policy
from ralph.policy.models import (
    PhaseCommitPolicy,
    PhaseDefinition,
    PhaseTransition,
    PipelinePolicy,
)


def _execution_result_event(phase: str, status: str) -> Event:
    return ExecutionResultEvent(phase=phase, status=status)


def _custom_policy(
    *,
    result_status_post_commit: dict[str, str] | None = None,
) -> PipelinePolicy:
    execution_data: dict[str, object] = {
        "drain": "builder",
        "role": "execution",
        "transitions": PhaseTransition(on_success="polisher"),
    }
    if result_status_post_commit is not None:
        execution_data["result_status_post_commit"] = result_status_post_commit
    return PipelinePolicy(
        entry_phase="builder",
        terminal_phase="done",
        phases={
            "builder": PhaseDefinition.model_validate(execution_data),
            "polisher": PhaseDefinition(
                drain="polisher",
                role="commit_cleanup",
                transitions=PhaseTransition(
                    on_success="savepoint",
                    on_loopback="polisher",
                    on_failure="halted",
                ),
            ),
            "savepoint": PhaseDefinition(
                drain="savepoint",
                role="commit",
                transitions=PhaseTransition(on_success="inspector", on_failure="halted"),
                commit_policy=PhaseCommitPolicy(
                    requires_artifact=True,
                    skipped_advances_progress=False,
                ),
            ),
            "inspector": PhaseDefinition(
                drain="inspector",
                role="analysis",
                transitions=PhaseTransition(on_success="done"),
            ),
            "done": PhaseDefinition(
                drain="done",
                role="terminal",
                terminal_outcome="success",
                transitions=PhaseTransition(on_success="done", on_loopback="done"),
            ),
            "halted": PhaseDefinition(
                drain="halted",
                role="terminal",
                terminal_outcome="failure",
                transitions=PhaseTransition(on_success="halted", on_loopback="halted"),
            ),
        },
        recovery={"failed_route": "halted"},
    )


def _advance_to_commit(state: PipelineState, policy: PipelinePolicy) -> PipelineState:
    cleanup_state, _ = reducer_reduce(state, PipelineEvent.AGENT_SUCCESS, policy)
    assert cleanup_state.phase == "savepoint"
    return cleanup_state


def test_partial_result_commits_then_returns_to_same_execution_phase_in_new_session() -> None:
    policy = _custom_policy(result_status_post_commit={"partial": "builder"})
    state = PipelineState(phase="builder", last_agent_session_id="session-1")

    cleanup_state, _ = reducer_reduce(
        state,
        _execution_result_event("builder", "partial"),
        policy,
    )
    commit_state = _advance_to_commit(cleanup_state, policy)
    next_state, _ = reducer_reduce(commit_state, PipelineEvent.COMMIT_SUCCESS, policy)

    assert cleanup_state.phase == "polisher"
    assert cleanup_state.post_commit_phase_override == "builder"
    assert next_state.phase == "builder"
    assert next_state.previous_phase == "savepoint"
    assert next_state.last_agent_session_id is None
    assert next_state.post_commit_phase_override is None


def test_completed_result_retains_commit_then_analyzer_flow() -> None:
    policy = _custom_policy(result_status_post_commit={"partial": "builder"})
    state = PipelineState(phase="builder", last_agent_session_id="session-1")

    cleanup_state, _ = reducer_reduce(
        state,
        _execution_result_event("builder", "completed"),
        policy,
    )
    commit_state = _advance_to_commit(cleanup_state, policy)
    next_state, _ = reducer_reduce(commit_state, PipelineEvent.COMMIT_SUCCESS, policy)

    assert cleanup_state.post_commit_phase_override is None
    assert next_state.phase == "inspector"
    assert next_state.previous_phase == "savepoint"


def test_partial_result_override_survives_checkpoint_round_trip_until_commit() -> None:
    policy = _custom_policy(result_status_post_commit={"partial": "builder"})
    state = PipelineState(phase="builder")

    cleanup_state, _ = reducer_reduce(
        state,
        _execution_result_event("builder", "partial"),
        policy,
    )
    restored = PipelineState.model_validate_json(cleanup_state.model_dump_json())
    commit_state = _advance_to_commit(restored, policy)
    next_state, _ = reducer_reduce(commit_state, PipelineEvent.COMMIT_SKIPPED, policy)

    assert restored.post_commit_phase_override == "builder"
    assert next_state.phase == "builder"
    assert next_state.post_commit_phase_override is None


def test_result_status_post_commit_target_must_reference_known_phase() -> None:
    with pytest.raises(ValueError, match=r"result_status_post_commit.*missing"):
        _custom_policy(result_status_post_commit={"partial": "missing"})


def test_default_policy_routes_partial_development_result_back_after_commit() -> None:
    defaults_dir = Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults"

    development = load_policy(defaults_dir).pipeline.phases["development"]

    assert development.result_status_post_commit == {"partial": "development"}
