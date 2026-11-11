from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from ralph.phases.phase_timing_record import PhaseTimingRecord
from ralph.pipeline.events import (
    AnalysisDecisionEvent,
    CommitResidualEvent,
    Event,
    ExecutionResultEvent,
    PipelineEvent,
)
from ralph.pipeline.reducer import reduce as reducer_reduce
from ralph.pipeline.state import PipelineState
from ralph.policy.loader import load_policy
from ralph.policy.models import (
    PhaseCommitPolicy,
    PhaseDefinition,
    PhaseTransition,
    PipelinePolicy,
)

pytestmark = pytest.mark.timeout_seconds(5)

_DEFAULTS_DIR = Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults"
_DEFAULT_PIPELINE_POLICY = load_policy(_DEFAULTS_DIR).pipeline


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
                role="commit_review",
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
    review_state, _ = reducer_reduce(state, PipelineEvent.AGENT_SUCCESS, policy)
    assert review_state.phase == "savepoint"
    return review_state


def _dev_timing(seconds: float) -> PhaseTimingRecord:
    return PhaseTimingRecord(
        phase="development",
        iteration=0,
        started_at=0.0,
        elapsed=timedelta(seconds=seconds),
        elapsed_seconds=int(seconds),
    )


def _state_with_dev_time(seconds: float, **kwargs: object) -> PipelineState:
    kwargs.setdefault("phase", "development")
    kwargs.setdefault("phase_timings", (_dev_timing(seconds),))
    return PipelineState(**kwargs)


def test_partial_result_commits_then_returns_to_same_execution_phase_in_new_session() -> None:
    policy = _custom_policy(result_status_post_commit={"partial": "builder"})
    state = PipelineState(phase="builder", last_agent_session_id="session-1")

    review_state, _ = reducer_reduce(
        state,
        _execution_result_event("builder", "partial"),
        policy,
    )
    commit_state = _advance_to_commit(review_state, policy)
    next_state, _ = reducer_reduce(commit_state, PipelineEvent.COMMIT_SUCCESS, policy)

    assert review_state.phase == "polisher"
    assert review_state.post_commit_phase_override == "builder"
    assert next_state.phase == "builder"
    assert next_state.previous_phase == "savepoint"
    assert next_state.last_agent_session_id is None
    assert next_state.post_commit_phase_override is None


def test_residual_commit_reinvokes_same_commit_phase_with_fresh_state() -> None:
    policy = _custom_policy()
    commit_state = PipelineState(
        phase="savepoint",
        commit={"message_prepared": True, "diff_prepared": True, "agent_invoked": True},
    )

    next_state, effects = reducer_reduce(
        commit_state,
        CommitResidualEvent(committed_paths=("right.py",), remaining_paths=("left.py",)),
        policy,
    )

    assert next_state.phase == "savepoint"
    assert next_state.previous_phase == "savepoint"
    assert next_state.commit.message_prepared is False
    assert next_state.commit.diff_prepared is False
    assert next_state.commit.agent_invoked is False
    assert effects == []


def test_unreceipted_commit_residual_enum_fails_closed_without_reentry() -> None:
    policy = _custom_policy()
    state = PipelineState(phase="savepoint")

    next_state, effects = reducer_reduce(state, PipelineEvent.COMMIT_RESIDUAL, policy)

    assert next_state == state
    assert effects == []


def test_typed_commit_residual_reenters_for_nonempty_receipt() -> None:
    policy = _custom_policy()
    state = PipelineState(phase="savepoint")

    next_state, effects = reducer_reduce(
        state,
        CommitResidualEvent(committed_paths=("right.py",), remaining_paths=("left.py",)),
        policy,
    )

    assert next_state.phase == "savepoint"
    assert effects == []


def test_completed_result_retains_commit_then_analyzer_flow() -> None:
    policy = _custom_policy(result_status_post_commit={"partial": "builder"})
    state = PipelineState(phase="builder", last_agent_session_id="session-1")

    review_state, _ = reducer_reduce(
        state,
        _execution_result_event("builder", "completed"),
        policy,
    )
    commit_state = _advance_to_commit(review_state, policy)
    next_state, _ = reducer_reduce(commit_state, PipelineEvent.COMMIT_SUCCESS, policy)

    assert review_state.post_commit_phase_override is None
    assert next_state.phase == "inspector"
    assert next_state.previous_phase == "savepoint"


def test_partial_result_override_survives_checkpoint_round_trip_until_commit() -> None:
    policy = _custom_policy(result_status_post_commit={"partial": "builder"})
    state = PipelineState(phase="builder")

    review_state, _ = reducer_reduce(
        state,
        _execution_result_event("builder", "partial"),
        policy,
    )
    restored = PipelineState.model_validate_json(review_state.model_dump_json())
    commit_state = _advance_to_commit(restored, policy)
    next_state, _ = reducer_reduce(commit_state, PipelineEvent.COMMIT_SKIPPED, policy)

    assert restored.post_commit_phase_override == "builder"
    assert next_state.phase == "builder"
    assert next_state.post_commit_phase_override is None


def test_result_status_post_commit_target_must_reference_known_phase() -> None:
    with pytest.raises(ValueError, match=r"result_status_post_commit.*missing"):
        _custom_policy(result_status_post_commit={"partial": "missing"})


def test_default_policy_routes_every_development_result_to_analysis_after_commit() -> None:
    policy = _DEFAULT_PIPELINE_POLICY

    development = policy.phases["development"]
    assert development.result_status_post_commit == {}

    for status in ("completed", "partial", "failed"):
        state = _state_with_dev_time(900.0)
        review_state, _ = reducer_reduce(
            state,
            _execution_result_event("development", status),
            policy,
        )
        commit_state, _ = reducer_reduce(
            review_state,
            PipelineEvent.AGENT_SUCCESS,
            policy,
        )
        analysis_state, _ = reducer_reduce(
            commit_state,
            PipelineEvent.COMMIT_SUCCESS,
            policy,
        )
        assert analysis_state.phase == "development_analysis"
        assert review_state.phase == "development_commit_review"
        assert commit_state.phase == "development_commit"


def test_failed_development_analysis_loops_back_to_development() -> None:
    policy = _DEFAULT_PIPELINE_POLICY

    next_state, _ = reducer_reduce(
        PipelineState(phase="development_analysis"),
        AnalysisDecisionEvent(phase="development_analysis", decision="failed"),
        policy,
    )

    assert next_state.phase == "development"
    assert next_state.pending_cycle_outcome is None


@pytest.mark.parametrize("completed_cycles", [0, 1])
def test_failed_analysis_preserves_outer_budget_on_loopback(
    completed_cycles: int,
) -> None:
    policy = _DEFAULT_PIPELINE_POLICY
    state = PipelineState(
        phase="development_analysis",
        budget_caps={"iteration": 2},
        outer_progress={"iteration": completed_cycles},
    )

    review_state, _ = reducer_reduce(
        state,
        AnalysisDecisionEvent(phase="development_analysis", decision="failed"),
        policy,
    )
    assert review_state.phase == "development"
    assert review_state.pending_cycle_outcome is None
    assert review_state.get_outer_progress("iteration") == completed_cycles


@pytest.mark.parametrize("completed_cycles", [0, 1])
def test_failed_analysis_does_not_enter_commit_path(
    completed_cycles: int,
) -> None:
    policy = _DEFAULT_PIPELINE_POLICY
    state = PipelineState(
        phase="development_analysis",
        budget_caps={"iteration": 2},
        outer_progress={"iteration": completed_cycles},
    )

    review_state, _ = reducer_reduce(
        state,
        AnalysisDecisionEvent(phase="development_analysis", decision="failed"),
        policy,
    )
    assert review_state.phase == "development"
    assert review_state.pending_cycle_outcome is None
    assert review_state.get_outer_progress("iteration") == completed_cycles


@pytest.mark.parametrize("status", ["completed", "partial", "failed"])
def test_development_result_regression_every_terminal_result_consumes_one_analysis_cycle(
    status: str,
) -> None:
    policy = _DEFAULT_PIPELINE_POLICY

    next_state, _ = reducer_reduce(
        _state_with_dev_time(900.0),
        _execution_result_event("development", status),
        policy,
    )

    assert next_state.phase == "development_commit_review"
    assert next_state.get_loop_iteration("development_analysis_iteration") == 0


def test_development_result_regression_analysis_loopback_does_not_double_charge_cycle() -> None:
    policy = _DEFAULT_PIPELINE_POLICY

    review_state, _ = reducer_reduce(
        _state_with_dev_time(900.0),
        _execution_result_event("development", "completed"),
        policy,
    )
    commit_state, _ = reducer_reduce(review_state, PipelineEvent.AGENT_SUCCESS, policy)
    analysis_state, _ = reducer_reduce(commit_state, PipelineEvent.COMMIT_SUCCESS, policy)
    next_state, _ = reducer_reduce(analysis_state, PipelineEvent.ANALYSIS_LOOPBACK, policy)

    assert analysis_state.phase == "development_analysis"
    assert next_state.phase == "development"
    assert next_state.get_loop_iteration("development_analysis_iteration") == 1


@pytest.mark.parametrize(
    ("seconds", "enters_analysis"),
    [(0.0, False), (899.9, False), (900.0, True), (900.1, True), (1200.0, True)],
)
def test_invocation_gate_threshold_controls_analysis_entry(
    seconds: float,
    enters_analysis: bool,
) -> None:
    policy = _DEFAULT_PIPELINE_POLICY

    state = _state_with_dev_time(seconds)
    review_state, _ = reducer_reduce(
        state, _execution_result_event("development", "completed"), policy
    )
    commit_state, _ = reducer_reduce(review_state, PipelineEvent.AGENT_SUCCESS, policy)
    next_state, _ = reducer_reduce(commit_state, PipelineEvent.COMMIT_SUCCESS, policy)

    if enters_analysis:
        assert next_state.phase == "development_analysis"
        assert next_state.get_loop_iteration("development_analysis_iteration") == 1
    else:
        assert next_state.phase == "development_final_commit_review"
        assert next_state.pending_cycle_outcome == "completed"
        assert next_state.get_loop_iteration("development_analysis_iteration") == 0


def test_invocation_gate_fractional_records_straddle_threshold() -> None:
    policy = _DEFAULT_PIPELINE_POLICY

    for total, enters in [(899.9, False), (900.0, True)]:
        half = total / 2
        timings = (_dev_timing(half), _dev_timing(half))
        state = PipelineState(phase="development", phase_timings=timings)
        review_state, _ = reducer_reduce(
            state, _execution_result_event("development", "completed"), policy
        )
        commit_state, _ = reducer_reduce(review_state, PipelineEvent.AGENT_SUCCESS, policy)
        next_state, _ = reducer_reduce(commit_state, PipelineEvent.COMMIT_SUCCESS, policy)

        if enters:
            assert next_state.phase == "development_analysis"
        else:
            assert next_state.phase == "development_final_commit_review"


def test_invocation_gate_skipped_analysis_sets_cycle_outcome() -> None:
    policy = _DEFAULT_PIPELINE_POLICY

    state = _state_with_dev_time(100.0)
    review_state, _ = reducer_reduce(
        state, _execution_result_event("development", "completed"), policy
    )
    commit_state, _ = reducer_reduce(review_state, PipelineEvent.AGENT_SUCCESS, policy)
    next_state, _ = reducer_reduce(commit_state, PipelineEvent.COMMIT_SUCCESS, policy)

    assert next_state.phase == "development_final_commit_review"
    assert next_state.pending_cycle_outcome == "completed"


def test_session_ceiling_partial_result_enters_analysis_regardless_of_time() -> None:
    policy = _DEFAULT_PIPELINE_POLICY

    state = _state_with_dev_time(100.0, last_agent_session_id="session-1")
    review_state, _ = reducer_reduce(
        state, _execution_result_event("development", "partial"), policy
    )
    assert review_state.phase == "development_commit_review"

    commit_state, _ = reducer_reduce(review_state, PipelineEvent.AGENT_SUCCESS, policy)
    assert commit_state.phase == "development_commit"

    next_state, _ = reducer_reduce(commit_state, PipelineEvent.COMMIT_SUCCESS, policy)
    assert next_state.phase == "development_analysis"
    assert next_state.get_loop_iteration("development_analysis_iteration") == 1


def test_failed_analysis_returns_to_development_for_every_outer_budget_state() -> None:
    policy = _DEFAULT_PIPELINE_POLICY

    for completed_cycles in (0, 1):
        state = PipelineState(
            phase="development_analysis",
            budget_caps={"iteration": 2},
            outer_progress={"iteration": completed_cycles},
        )
        review_state, _ = reducer_reduce(
            state,
            AnalysisDecisionEvent(phase="development_analysis", decision="failed"),
            policy,
        )
        assert review_state.phase == "development"
        assert review_state.get_outer_progress("iteration") == completed_cycles


def test_cycle_timing_start_index_resets_on_lifecycle_commit() -> None:
    policy = _DEFAULT_PIPELINE_POLICY

    old_timing = _dev_timing(900.0)
    new_timing = _dev_timing(10.0)
    state = PipelineState(
        phase="development_final_commit",
        phase_timings=(old_timing, new_timing),
        cycle_timing_start_index=1,
        budget_caps={"iteration": 5},
        outer_progress={"iteration": 0},
        pending_cycle_outcome="completed",
    )

    commit_state, _ = reducer_reduce(state, PipelineEvent.COMMIT_SUCCESS, policy)

    assert commit_state.phase == "planning"
    assert commit_state.cycle_timing_start_index == 2

    next_dev_state = PipelineState(
        phase="development",
        phase_timings=(old_timing, new_timing, _dev_timing(10.0)),
        cycle_timing_start_index=2,
    )
    review_state, _ = reducer_reduce(
        next_dev_state, _execution_result_event("development", "completed"), policy
    )
    commit_state2, _ = reducer_reduce(review_state, PipelineEvent.AGENT_SUCCESS, policy)
    next_state, _ = reducer_reduce(commit_state2, PipelineEvent.COMMIT_SUCCESS, policy)

    assert next_state.phase == "development_final_commit_review"


@pytest.mark.parametrize("status", ["partial", "failed"])
def test_always_invoke_statuses_enter_analysis_below_threshold(status: str) -> None:
    policy = _DEFAULT_PIPELINE_POLICY

    state = _state_with_dev_time(10.0)
    review_state, _ = reducer_reduce(state, _execution_result_event("development", status), policy)
    commit_state, _ = reducer_reduce(review_state, PipelineEvent.AGENT_SUCCESS, policy)
    next_state, _ = reducer_reduce(commit_state, PipelineEvent.COMMIT_SUCCESS, policy)

    assert next_state.phase == "development_analysis"
    assert next_state.get_loop_iteration("development_analysis_iteration") == 1


def test_completed_below_threshold_skips_analysis_and_charges_no_cycle() -> None:
    policy = _DEFAULT_PIPELINE_POLICY

    state = _state_with_dev_time(10.0)
    review_state, _ = reducer_reduce(
        state, _execution_result_event("development", "completed"), policy
    )
    commit_state, _ = reducer_reduce(review_state, PipelineEvent.AGENT_SUCCESS, policy)
    next_state, _ = reducer_reduce(commit_state, PipelineEvent.COMMIT_SUCCESS, policy)

    assert next_state.phase == "development_final_commit_review"
    assert next_state.pending_cycle_outcome == "completed"
    assert next_state.get_loop_iteration("development_analysis_iteration") == 0


def test_result_status_does_not_leak_across_cycles() -> None:
    policy = _DEFAULT_PIPELINE_POLICY

    state = _state_with_dev_time(10.0)
    review_state, _ = reducer_reduce(
        state, _execution_result_event("development", "partial"), policy
    )
    commit_state, _ = reducer_reduce(review_state, PipelineEvent.AGENT_SUCCESS, policy)
    analysis_state, _ = reducer_reduce(commit_state, PipelineEvent.COMMIT_SUCCESS, policy)
    assert analysis_state.phase == "development_analysis"
    assert analysis_state.last_execution_result_status is None

    dev_state, _ = reducer_reduce(analysis_state, PipelineEvent.ANALYSIS_LOOPBACK, policy)
    assert dev_state.phase == "development"
    assert dev_state.last_execution_result_status is None
