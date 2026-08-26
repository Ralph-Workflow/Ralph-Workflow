"""Black-box routing tests for partial execution results."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from ralph.phases.phase_timing_record import PhaseTimingRecord
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


def _dev_timing(seconds: float) -> PhaseTimingRecord:
    """Create a development phase timing record with the given elapsed seconds."""
    return PhaseTimingRecord(
        phase="development",
        iteration=0,
        started_at=0.0,
        elapsed=timedelta(seconds=seconds),
        elapsed_seconds=int(seconds),
    )


def _state_with_dev_time(seconds: float, **kwargs: object) -> PipelineState:
    """Create a development-phase state with a single timing record."""
    return PipelineState(
        phase="development",
        phase_timings=(_dev_timing(seconds),),
        **kwargs,
    )


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


def test_residual_commit_reinvokes_same_commit_phase_with_fresh_state() -> None:
    policy = _custom_policy()
    commit_state = PipelineState(
        phase="savepoint",
        commit={"message_prepared": True, "diff_prepared": True, "agent_invoked": True},
    )

    next_state, effects = reducer_reduce(commit_state, PipelineEvent.COMMIT_RESIDUAL, policy)

    assert next_state.phase == "savepoint"
    assert next_state.previous_phase == "savepoint"
    assert next_state.commit.message_prepared is False
    assert next_state.commit.diff_prepared is False
    assert next_state.commit.agent_invoked is False
    assert effects == []


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
    policy = load_policy(defaults_dir).pipeline

    development = policy.phases["development"]
    assert development.result_status_post_commit == {}

    for status in ("completed", "partial", "failed"):
        state = _state_with_dev_time(900.0)
        cleanup_state, _ = reducer_reduce(
            state,
            _execution_result_event("development", status),
            policy,
        )
        commit_state, _ = reducer_reduce(
            cleanup_state,
            PipelineEvent.AGENT_SUCCESS,
            policy,
        )
        analysis_state, _ = reducer_reduce(
            commit_state,
            PipelineEvent.COMMIT_SUCCESS,
            policy,
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


@pytest.mark.parametrize(
    ("completed_cycles", "expected_phase"),
    [(0, "planning"), (1, "failed_terminal")],
)
def test_failed_cycle_skipped_commit_replans_only_while_budget_remains(
    completed_cycles: int,
    expected_phase: str,
) -> None:
    """S-3: a durable no-diff failed cycle still consumes budget and respects the gate."""
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
    next_state, _ = reducer_reduce(commit_state, PipelineEvent.COMMIT_SKIPPED, policy)

    assert cleanup_state.phase == "development_final_commit_cleanup"
    assert commit_state.phase == "development_final_commit"
    assert next_state.phase == expected_phase
    assert next_state.pending_cycle_outcome is None
    assert next_state.get_outer_progress("iteration") == completed_cycles + 1


@pytest.mark.parametrize("status", ["completed", "partial", "failed"])
def test_development_result_regression_every_terminal_result_consumes_one_analysis_cycle(
    status: str,
) -> None:
    """S-1: terminal development results charge analysis at the post-commit seam, not before."""
    defaults_dir = Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults"
    policy = load_policy(defaults_dir).pipeline

    next_state, _ = reducer_reduce(
        _state_with_dev_time(900.0),
        _execution_result_event("development", status),
        policy,
    )

    assert next_state.phase == "development_commit_cleanup"
    # The analysis counter is NOT charged at execution-result time;
    # it is charged at the post-commit seam when analysis is entered.
    assert next_state.get_loop_iteration("development_analysis_iteration") == 0


def test_development_result_regression_analysis_loopback_does_not_double_charge_cycle() -> None:
    """S-1: the charged result cycle remains one when its analysis requests changes."""
    defaults_dir = Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults"
    policy = load_policy(defaults_dir).pipeline

    cleanup_state, _ = reducer_reduce(
        _state_with_dev_time(900.0),
        _execution_result_event("development", "completed"),
        policy,
    )
    commit_state, _ = reducer_reduce(cleanup_state, PipelineEvent.AGENT_SUCCESS, policy)
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
    """S-1: cumulative unrounded dev time at or above 900.0s enters analysis."""
    defaults_dir = Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults"
    policy = load_policy(defaults_dir).pipeline

    state = _state_with_dev_time(seconds)
    cleanup_state, _ = reducer_reduce(
        state, _execution_result_event("development", "completed"), policy
    )
    commit_state, _ = reducer_reduce(cleanup_state, PipelineEvent.AGENT_SUCCESS, policy)
    next_state, _ = reducer_reduce(commit_state, PipelineEvent.COMMIT_SUCCESS, policy)

    if enters_analysis:
        assert next_state.phase == "development_analysis"
        assert next_state.get_loop_iteration("development_analysis_iteration") == 1
    else:
        assert next_state.phase == "development_final_commit_cleanup"
        assert next_state.pending_cycle_outcome == "completed"
        assert next_state.get_loop_iteration("development_analysis_iteration") == 0


def test_invocation_gate_fractional_records_straddle_threshold() -> None:
    """S-1: split fractional records totaling 899.9 skip; 900.0 enter analysis."""
    defaults_dir = Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults"
    policy = load_policy(defaults_dir).pipeline

    for total, enters in [(899.9, False), (900.0, True)]:
        half = total / 2
        timings = (_dev_timing(half), _dev_timing(half))
        state = PipelineState(phase="development", phase_timings=timings)
        cleanup_state, _ = reducer_reduce(
            state, _execution_result_event("development", "completed"), policy
        )
        commit_state, _ = reducer_reduce(cleanup_state, PipelineEvent.AGENT_SUCCESS, policy)
        next_state, _ = reducer_reduce(commit_state, PipelineEvent.COMMIT_SUCCESS, policy)

        if enters:
            assert next_state.phase == "development_analysis"
        else:
            assert next_state.phase == "development_final_commit_cleanup"


def test_invocation_gate_skipped_analysis_sets_cycle_outcome() -> None:
    """S-1: when analysis is skipped by the gate, the completed cycle outcome is set."""
    defaults_dir = Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults"
    policy = load_policy(defaults_dir).pipeline

    state = _state_with_dev_time(100.0)
    cleanup_state, _ = reducer_reduce(
        state, _execution_result_event("development", "completed"), policy
    )
    commit_state, _ = reducer_reduce(cleanup_state, PipelineEvent.AGENT_SUCCESS, policy)
    next_state, _ = reducer_reduce(commit_state, PipelineEvent.COMMIT_SUCCESS, policy)

    assert next_state.phase == "development_final_commit_cleanup"
    assert next_state.pending_cycle_outcome == "completed"


def test_session_ceiling_partial_result_enters_analysis_regardless_of_time() -> None:
    """S-2: a session-ceiling partial result commits then enters analysis even below threshold."""
    defaults_dir = Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults"
    policy = load_policy(defaults_dir).pipeline

    state = _state_with_dev_time(100.0, last_agent_session_id="session-1")
    cleanup_state, _ = reducer_reduce(
        state, _execution_result_event("development", "partial"), policy
    )
    # The partial result routes to commit cleanup first
    assert cleanup_state.phase == "development_commit_cleanup"

    commit_state, _ = reducer_reduce(cleanup_state, PipelineEvent.AGENT_SUCCESS, policy)
    assert commit_state.phase == "development_commit"

    next_state, _ = reducer_reduce(commit_state, PipelineEvent.COMMIT_SUCCESS, policy)
    # Below the threshold, partial status still enters analysis (always_invoke_statuses)
    assert next_state.phase == "development_analysis"
    assert next_state.get_loop_iteration("development_analysis_iteration") == 1


def test_failed_analysis_replans_while_budget_remains() -> None:
    """S-1: failed analyzer decision replans to planning only when outer budget remains."""
    defaults_dir = Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults"
    policy = load_policy(defaults_dir).pipeline

    for completed_cycles, expected_phase in [(0, "planning"), (1, "failed_terminal")]:
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

        assert next_state.phase == expected_phase
        assert next_state.get_outer_progress("iteration") == completed_cycles + 1


def test_cycle_timing_start_index_resets_on_lifecycle_commit() -> None:
    """S-1: earlier cycle timings cannot make a short later cycle eligible for analysis."""
    defaults_dir = Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults"
    policy = load_policy(defaults_dir).pipeline

    # Simulate a state at development_final_commit with one prior cycle's timing (900s)
    # plus the current cycle's short timing (10s).
    old_timing = _dev_timing(900.0)
    new_timing = _dev_timing(10.0)
    state = PipelineState(
        phase="development_final_commit",
        phase_timings=(old_timing, new_timing),
        cycle_timing_start_index=1,  # current cycle starts at index 1
        budget_caps={"iteration": 5},
        outer_progress={"iteration": 0},
        pending_cycle_outcome="completed",
    )

    commit_state, _ = reducer_reduce(state, PipelineEvent.COMMIT_SUCCESS, policy)

    # The lifecycle commit should advance to planning (budget remaining)
    # and reset cycle_timing_start_index so the old 900s record is excluded.
    assert commit_state.phase == "planning"
    assert commit_state.cycle_timing_start_index == 2  # len(phase_timings)

    # Now if the next development cycle is short, it should NOT enter analysis
    # because only the new cycle's 10s is counted.
    next_dev_state = PipelineState(
        phase="development",
        phase_timings=(old_timing, new_timing, _dev_timing(10.0)),
        cycle_timing_start_index=2,
    )
    cleanup_state, _ = reducer_reduce(
        next_dev_state, _execution_result_event("development", "completed"), policy
    )
    commit_state2, _ = reducer_reduce(cleanup_state, PipelineEvent.AGENT_SUCCESS, policy)
    next_state, _ = reducer_reduce(commit_state2, PipelineEvent.COMMIT_SUCCESS, policy)

    assert next_state.phase == "development_final_commit_cleanup"


@pytest.mark.parametrize("status", ["partial", "failed"])
def test_always_invoke_statuses_enter_analysis_below_threshold(status: str) -> None:
    """S-2: partial and failed results enter analysis even below the 900s threshold."""
    defaults_dir = Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults"
    policy = load_policy(defaults_dir).pipeline

    state = _state_with_dev_time(10.0)
    cleanup_state, _ = reducer_reduce(
        state, _execution_result_event("development", status), policy
    )
    commit_state, _ = reducer_reduce(cleanup_state, PipelineEvent.AGENT_SUCCESS, policy)
    next_state, _ = reducer_reduce(commit_state, PipelineEvent.COMMIT_SUCCESS, policy)

    assert next_state.phase == "development_analysis"
    assert next_state.get_loop_iteration("development_analysis_iteration") == 1


def test_completed_below_threshold_skips_analysis_and_charges_no_cycle() -> None:
    """S-2: completed results below 900s skip analysis per the retained time bypass."""
    defaults_dir = Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults"
    policy = load_policy(defaults_dir).pipeline

    state = _state_with_dev_time(10.0)
    cleanup_state, _ = reducer_reduce(
        state, _execution_result_event("development", "completed"), policy
    )
    commit_state, _ = reducer_reduce(cleanup_state, PipelineEvent.AGENT_SUCCESS, policy)
    next_state, _ = reducer_reduce(commit_state, PipelineEvent.COMMIT_SUCCESS, policy)

    assert next_state.phase == "development_final_commit_cleanup"
    assert next_state.pending_cycle_outcome == "completed"
    assert next_state.get_loop_iteration("development_analysis_iteration") == 0


def test_result_status_does_not_leak_across_cycles() -> None:
    """S-2: a partial result's status is cleared after the gate so it cannot force the next cycle."""
    defaults_dir = Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults"
    policy = load_policy(defaults_dir).pipeline

    # Cycle 1: partial result below threshold enters analysis via always_invoke_statuses
    state = _state_with_dev_time(10.0)
    cleanup_state, _ = reducer_reduce(
        state, _execution_result_event("development", "partial"), policy
    )
    commit_state, _ = reducer_reduce(cleanup_state, PipelineEvent.AGENT_SUCCESS, policy)
    analysis_state, _ = reducer_reduce(commit_state, PipelineEvent.COMMIT_SUCCESS, policy)
    assert analysis_state.phase == "development_analysis"
    # The status must have been cleared at the post-commit seam
    assert analysis_state.last_execution_result_status is None

    # The analysis loopback returns to development for the next cycle
    dev_state, _ = reducer_reduce(analysis_state, PipelineEvent.ANALYSIS_LOOPBACK, policy)
    assert dev_state.phase == "development"
    assert dev_state.last_execution_result_status is None
