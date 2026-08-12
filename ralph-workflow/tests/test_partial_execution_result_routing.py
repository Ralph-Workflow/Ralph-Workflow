"""Black-box routing tests for partial execution results."""

from __future__ import annotations

from pathlib import Path

import pytest

from ralph.pipeline.events import AnalysisDecisionEvent, Event, ExecutionResultEvent, PipelineEvent
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


def test_default_policy_routes_every_development_result_to_analysis_after_commit() -> None:
    defaults_dir = Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults"

    development = load_policy(defaults_dir).pipeline.phases["development"]

    assert development.result_status_post_commit == {}

    for status in ("completed", "partial", "failed"):
        cleanup_state, _ = reducer_reduce(
            PipelineState(phase="development"),
            _execution_result_event("development", status),
            load_policy(defaults_dir).pipeline,
        )
        commit_state, _ = reducer_reduce(
            cleanup_state,
            PipelineEvent.AGENT_SUCCESS,
            load_policy(defaults_dir).pipeline,
        )
        analysis_state, _ = reducer_reduce(
            commit_state,
            PipelineEvent.COMMIT_SUCCESS,
            load_policy(defaults_dir).pipeline,
        )
        assert analysis_state.phase == "development_analysis"
        assert cleanup_state.phase == "development_commit_cleanup"
        assert commit_state.phase == "development_commit"


def test_failed_development_analysis_closes_cycle_through_final_commit() -> None:
    """A terminal analyzer decision ends this cycle at the commit boundary."""
    defaults_dir = Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults"
    policy = load_policy(defaults_dir).pipeline

    next_state, _ = reducer_reduce(
        PipelineState(phase="development_analysis"),
        AnalysisDecisionEvent(phase="development_analysis", decision="failed"),
        policy,
    )

    assert next_state.phase == "development_final_commit_cleanup"
    assert next_state.pending_cycle_outcome == "failed"


@pytest.mark.parametrize(
    ("completed_cycles", "expected_phase"),
    [(0, "planning"), (1, "failed_terminal")],
)
def test_failed_cycle_commits_then_replans_only_while_budget_remains(
    completed_cycles: int,
    expected_phase: str,
) -> None:
    """Failed cycles are durable before the global budget chooses replan or exit."""
    defaults_dir = Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults"
    policy = load_policy(defaults_dir).pipeline
    state = PipelineState(
        phase="development_analysis",
        budget_caps={"iteration": 2},
        outer_progress={"iteration": completed_cycles},
    )

    cleanup_state, _ = reducer_reduce(
        state,
        AnalysisDecisionEvent(phase="development_analysis", decision="failed"),
        policy,
    )
    commit_state, _ = reducer_reduce(cleanup_state, PipelineEvent.AGENT_SUCCESS, policy)
    next_state, _ = reducer_reduce(commit_state, PipelineEvent.COMMIT_SUCCESS, policy)

    assert cleanup_state.phase == "development_final_commit_cleanup"
    assert commit_state.phase == "development_final_commit"
    assert next_state.phase == expected_phase
    assert next_state.pending_cycle_outcome is None
    assert next_state.get_outer_progress("iteration") == completed_cycles + 1


@pytest.mark.parametrize("status", ["completed", "partial", "failed"])
def test_development_result_regression_every_terminal_result_consumes_one_analysis_cycle(
    status: str,
) -> None:
    """S-1: terminal development results advance the declared analysis counter once."""
    defaults_dir = Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults"
    policy = load_policy(defaults_dir).pipeline

    next_state, _ = reducer_reduce(
        PipelineState(phase="development"),
        _execution_result_event("development", status),
        policy,
    )

    assert next_state.phase == "development_commit_cleanup"
    assert next_state.get_loop_iteration("development_analysis_iteration") == 1


def test_development_result_regression_analysis_loopback_does_not_double_charge_cycle() -> None:
    """S-1: the charged result cycle remains one when its analysis requests changes."""
    defaults_dir = Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults"
    policy = load_policy(defaults_dir).pipeline

    cleanup_state, _ = reducer_reduce(
        PipelineState(phase="development"),
        _execution_result_event("development", "completed"),
        policy,
    )
    commit_state, _ = reducer_reduce(cleanup_state, PipelineEvent.AGENT_SUCCESS, policy)
    analysis_state, _ = reducer_reduce(commit_state, PipelineEvent.COMMIT_SUCCESS, policy)
    next_state, _ = reducer_reduce(analysis_state, PipelineEvent.ANALYSIS_LOOPBACK, policy)

    assert analysis_state.phase == "development_analysis"
    assert next_state.phase == "development"
    assert next_state.get_loop_iteration("development_analysis_iteration") == 1
