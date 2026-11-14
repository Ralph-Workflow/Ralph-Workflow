from __future__ import annotations

import functools
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.phases.commit_review import CommitReviewRejected
from ralph.phases.phase_timing_record import PhaseTimingRecord
from ralph.pipeline.cycle_timing import (
    RoutingTiming,
    apply_development_timebox,
    conclude_cycle_on_route_out_of_cycle,
    conclude_development_timebox_on_route_out_of_development,
)
from ralph.pipeline.events import AnalysisDecisionEvent, PipelineEvent
from ralph.pipeline.progress import apply_commit_outcome
from ralph.pipeline.reducer import reduce
from ralph.pipeline.state import PipelineState
from ralph.policy.loader import load_policy

if TYPE_CHECKING:
    from ralph.policy.models import PipelinePolicy

RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "rollback_pending at the rejection ceiling and after the cycle timebox": (
        "a rejection that skipped the rollback left the rejected tree in place, so "
        "recovery re-entered commit review and rejected the same tree on every pass; "
        "every rejection now requests the rollback and the assertion pins that"
    ),
}

_DEFAULTS_DIR = Path(__file__).resolve().parents[2] / "ralph" / "policy" / "defaults"


@functools.cache
def _policy() -> PipelinePolicy:
    return load_policy(_DEFAULTS_DIR).pipeline


def _dev_commit_entry(policy: PipelinePolicy) -> str:
    assert policy.development_timebox is not None
    return policy.development_timebox.end_entry


def _cycle_commit_entry(policy: PipelinePolicy) -> str:
    assert policy.cycle_timebox is not None
    return policy.cycle_timebox.end_entry


def _phase_before_dev_commit(policy: PipelinePolicy) -> str:
    target = _dev_commit_entry(policy)
    for name, phase in policy.phases.items():
        if phase.transitions.on_success == target:
            return name
    msg = f"No phase found that transitions on success to {target!r}"
    raise AssertionError(msg)


def _phase_before_cycle_commit(policy: PipelinePolicy) -> str:
    target = _cycle_commit_entry(policy)
    for name, phase in policy.phases.items():
        if phase.transitions.on_success == target:
            return name
    msg = f"No phase found that transitions on success to {target!r}"
    raise AssertionError(msg)


def _analysis_phase_looping_back_to_development(policy: PipelinePolicy) -> str:
    dev_start = policy.development_timebox.start_entry if policy.development_timebox else None
    for name, phase in policy.phases.items():
        route = phase.decisions.get("request_changes")
        if route is not None and route.target == dev_start:
            return name
    msg = "no analysis phase routes request_changes back to development"
    raise AssertionError(msg)


def _sample_timing(
    phase: str,
    iteration: int = 0,
    elapsed_seconds: int = 10,
) -> PhaseTimingRecord:
    return PhaseTimingRecord(
        phase=phase,
        iteration=iteration,
        started_at=100.0,
        elapsed=timedelta(seconds=elapsed_seconds),
        elapsed_seconds=elapsed_seconds,
    )


def _outer_budget_counter(policy: PipelinePolicy) -> str:
    for name, counter in policy.budget_counters.items():
        if counter.tracks_budget:
            return name
    msg = "policy declares no tracked budget counter"
    raise AssertionError(msg)


def _commit_accounting(
    state: PipelineState,
    *,
    reset_fields: tuple[str, ...],
    counter_name: str,
) -> tuple[tuple[int, ...], int, int]:
    return (
        tuple(state.get_loop_iteration(field) for field in reset_fields),
        state.get_outer_progress(counter_name),
        state.cycle_timing_start_index,
    )


def test_development_timebox_concludes_on_entry_to_commit_phase_via_reducer() -> None:
    policy = _policy()
    pre_commit = _phase_before_dev_commit(policy)
    commit_phase = _dev_commit_entry(policy)

    state = PipelineState(
        phase=pre_commit,
        dev_timebox_active=True,
        dev_timebox_consumed_seconds=1500.0,
        dev_timebox_started_at_epoch=4000.0,
    )

    next_state, _ = reduce(
        state,
        PipelineEvent.AGENT_SUCCESS,
        policy,
        routing_timing=RoutingTiming(total_elapsed_seconds=1500.0),
    )

    assert next_state.phase == commit_phase
    assert next_state.dev_timebox_active is False
    assert next_state.dev_timebox_consumed_seconds == 0.0
    assert next_state.dev_timebox_started_at_epoch is None


def test_development_timebox_conclude_function_pure_semantics() -> None:
    policy = _policy()
    commit_phase = _dev_commit_entry(policy)

    active_state = PipelineState(
        phase="development",
        dev_timebox_active=True,
        dev_timebox_consumed_seconds=1200.0,
        dev_timebox_started_at_epoch=3000.0,
    )

    concluded = conclude_development_timebox_on_route_out_of_development(
        active_state,
        commit_phase,
        policy=policy,
    )
    assert concluded.dev_timebox_active is False
    assert concluded.dev_timebox_consumed_seconds == 0.0
    assert concluded.dev_timebox_started_at_epoch is None

    preserved = conclude_development_timebox_on_route_out_of_development(
        active_state,
        "development_analysis",
        policy=policy,
    )
    assert preserved.dev_timebox_active is True
    assert preserved.dev_timebox_consumed_seconds == 1200.0
    assert preserved.dev_timebox_started_at_epoch == 3000.0


def test_development_timebox_remains_active_when_routing_to_pre_commit_phase() -> None:
    policy = _policy()
    pre_commit = _phase_before_dev_commit(policy)

    state = PipelineState(
        phase="development",
        dev_timebox_active=True,
        dev_timebox_consumed_seconds=800.0,
        dev_timebox_started_at_epoch=2000.0,
    )

    next_state, _ = reduce(
        state,
        PipelineEvent.AGENT_SUCCESS,
        policy,
        routing_timing=RoutingTiming(total_elapsed_seconds=800.0),
    )

    assert next_state.phase == pre_commit
    assert next_state.dev_timebox_active is True
    assert next_state.dev_timebox_consumed_seconds == 800.0


def test_cycle_timebox_concludes_on_entry_to_final_commit_via_reducer() -> None:
    policy = _policy()
    pre_final_commit = _phase_before_cycle_commit(policy)
    final_commit = _cycle_commit_entry(policy)

    state = PipelineState(
        phase=pre_final_commit,
        cycle_timebox_active=True,
        cycle_timebox_consumed_seconds=3200.0,
        cycle_timebox_started_at_epoch=1000.0,
    )

    next_state, _ = reduce(
        state,
        PipelineEvent.AGENT_SUCCESS,
        policy,
        routing_timing=RoutingTiming(total_elapsed_seconds=3200.0),
    )

    assert next_state.phase == final_commit
    assert next_state.cycle_timebox_active is False
    assert next_state.cycle_timebox_consumed_seconds == 3200.0


def test_cycle_timebox_conclude_function_pure_semantics() -> None:
    policy = _policy()
    final_commit = _cycle_commit_entry(policy)

    active_state = PipelineState(
        phase="development_analysis",
        cycle_timebox_active=True,
        cycle_timebox_consumed_seconds=2750.0,
    )

    concluded = conclude_cycle_on_route_out_of_cycle(
        active_state,
        final_commit,
        policy=policy,
    )
    assert concluded.cycle_timebox_active is False
    assert concluded.cycle_timebox_consumed_seconds == 2750.0

    pre_final_commit = _phase_before_cycle_commit(policy)
    preserved = conclude_cycle_on_route_out_of_cycle(
        active_state,
        pre_final_commit,
        policy=policy,
    )
    assert preserved.cycle_timebox_active is True
    assert preserved.cycle_timebox_consumed_seconds == 2750.0


def test_cycle_timebox_remains_active_entering_pre_final_commit_phase() -> None:
    policy = _policy()
    pre_final_commit = _phase_before_cycle_commit(policy)

    state = PipelineState(
        phase="development_analysis",
        cycle_timebox_active=True,
        cycle_timebox_consumed_seconds=2100.0,
    )

    event = AnalysisDecisionEvent(phase="development_analysis", decision="completed")
    next_state, _ = reduce(
        state, event, policy, routing_timing=RoutingTiming(total_elapsed_seconds=2100.0)
    )

    assert next_state.phase == pre_final_commit
    assert next_state.cycle_timebox_active is True
    assert next_state.cycle_timebox_consumed_seconds == 2100.0


def test_commit_completion_resets_lifecycle_phase_counters_and_timing_window() -> None:
    policy = _policy()
    final_commit = _cycle_commit_entry(policy)
    lifecycle = policy.lifecycle_phases.get(final_commit)
    assert lifecycle is not None
    assert lifecycle.increments_counter is not None and lifecycle.increments_counter != "none"
    assert len(lifecycle.loop_resets) > 0

    timings = (
        _sample_timing("development", iteration=0),
        _sample_timing("development", iteration=0),
        _sample_timing("development_analysis", iteration=0),
    )

    state = PipelineState(
        phase=final_commit,
        phase_timings=timings,
        cycle_timing_start_index=0,
    )
    for reset_field in lifecycle.loop_resets:
        state = state.with_loop_iteration(reset_field, 5)

    counter_name = lifecycle.increments_counter
    state = state.with_outer_progress(counter_name, 2)

    result = apply_commit_outcome(state, state, skipped=False, policy=policy)

    for reset_field in lifecycle.loop_resets:
        assert result.get_loop_iteration(reset_field) == 0

    assert result.get_outer_progress(counter_name) == 3

    assert result.cycle_timing_start_index == len(timings)


def test_commit_completion_via_reducer_applies_lifecycle_resets() -> None:
    policy = _policy()
    final_commit = _cycle_commit_entry(policy)
    lifecycle = policy.lifecycle_phases.get(final_commit)
    assert lifecycle is not None

    timings = (_sample_timing("development", iteration=0),)
    state = PipelineState(
        phase=final_commit,
        phase_timings=timings,
        cycle_timing_start_index=0,
    )
    for reset_field in lifecycle.loop_resets:
        state = state.with_loop_iteration(reset_field, 3)

    counter_name = lifecycle.increments_counter
    assert counter_name is not None
    state = state.with_outer_progress(counter_name, 1)

    next_state, _ = reduce(
        state,
        PipelineEvent.COMMIT_SUCCESS,
        policy,
        routing_timing=RoutingTiming(total_elapsed_seconds=100.0),
    )

    for reset_field in lifecycle.loop_resets:
        assert next_state.get_loop_iteration(reset_field) == 0
    assert next_state.get_outer_progress(counter_name) == 2
    assert next_state.cycle_timing_start_index == len(timings)


def test_intermediate_commit_completion_resets_commit_policy_loop_counters() -> None:
    policy = _policy()
    dev_commit = _dev_commit_entry(policy)
    phase_def = policy.phases.get(dev_commit)
    assert phase_def is not None and phase_def.commit_policy is not None
    commit_policy = phase_def.commit_policy
    assert len(commit_policy.loop_resets) > 0

    timings = (_sample_timing("development", iteration=0),)
    state = PipelineState(
        phase=dev_commit,
        phase_timings=timings,
        cycle_timing_start_index=0,
    )
    for reset_field in commit_policy.loop_resets:
        state = state.with_loop_iteration(reset_field, 4)

    result = apply_commit_outcome(state, state, skipped=False, policy=policy)

    for reset_field in commit_policy.loop_resets:
        assert result.get_loop_iteration(reset_field) == 0

    assert result.cycle_timing_start_index == 0


def test_commit_then_development_restarts_development_timer_from_zero() -> None:
    policy = _policy()
    assert policy.development_timebox is not None
    dev_start = policy.development_timebox.start_entry

    state = PipelineState(
        phase="development_analysis",
        dev_timebox_active=False,
        dev_timebox_consumed_seconds=0.0,
        dev_timebox_started_at_epoch=None,
    )

    decision_event = AnalysisDecisionEvent(phase="development_analysis", decision="request_changes")
    next_state, _ = reduce(
        state,
        decision_event,
        policy,
        routing_timing=RoutingTiming(
            total_elapsed_seconds=2000.0,
            development_elapsed_seconds=0.0,
            current_epoch=7500.0,
        ),
    )

    assert next_state.phase == dev_start
    assert next_state.dev_timebox_active is True
    assert next_state.dev_timebox_consumed_seconds == 0.0
    assert next_state.dev_timebox_started_at_epoch == 7500.0
    assert next_state.dev_timebox_redirect_reason is None


def test_apply_development_timebox_starts_inactive_timer_on_entry_to_start_phase() -> None:
    policy = _policy()
    assert policy.development_timebox is not None
    dev_start = policy.development_timebox.start_entry

    inactive_state = PipelineState(
        phase="development_analysis",
        dev_timebox_active=False,
        dev_timebox_consumed_seconds=0.0,
        dev_timebox_started_at_epoch=None,
    )

    decision = apply_development_timebox(
        inactive_state,
        dev_start,
        policy=policy,
        routing_timing=RoutingTiming(
            total_elapsed_seconds=100.0,
            current_epoch=9000.0,
        ),
    )

    assert decision.timing_started is True
    assert decision.redirected is False
    assert decision.state.dev_timebox_active is True
    assert decision.state.dev_timebox_consumed_seconds == 0.0
    assert decision.state.dev_timebox_started_at_epoch == 9000.0


def test_development_reentry_while_already_active_preserves_consumed_seconds() -> None:
    policy = _policy()
    assert policy.development_timebox is not None
    dev_start = policy.development_timebox.start_entry

    active_state = PipelineState(
        phase=dev_start,
        dev_timebox_active=True,
        dev_timebox_consumed_seconds=1800.0,
        dev_timebox_started_at_epoch=4000.0,
    )

    decision = apply_development_timebox(
        active_state,
        dev_start,
        policy=policy,
        routing_timing=RoutingTiming(
            total_elapsed_seconds=2000.0,
            development_elapsed_seconds=1800.0,
            current_epoch=5800.0,
        ),
    )

    assert decision.timing_started is False
    assert decision.state.dev_timebox_active is True
    assert decision.state.dev_timebox_consumed_seconds == 1800.0
    assert decision.state.dev_timebox_started_at_epoch == 4000.0


def test_commit_then_development_route_concludes_and_restarts_the_development_timer() -> None:
    policy = _policy()
    assert policy.development_timebox is not None
    dev_start = policy.development_timebox.start_entry
    commit_phase = _dev_commit_entry(policy)
    review_phase = _phase_before_dev_commit(policy)
    analysis_phase = _analysis_phase_looping_back_to_development(policy)

    timings = (_sample_timing("development", iteration=0, elapsed_seconds=1000),)
    state = PipelineState(
        phase=review_phase,
        dev_timebox_active=True,
        dev_timebox_consumed_seconds=1500.0,
        dev_timebox_started_at_epoch=4000.0,
        cycle_timebox_active=True,
        cycle_timebox_consumed_seconds=2100.0,
        cycle_timebox_started_at_epoch=1000.0,
        phase_timings=timings,
        cycle_timing_start_index=0,
    )

    committed, _ = reduce(
        state,
        PipelineEvent.AGENT_SUCCESS,
        policy,
        routing_timing=RoutingTiming(
            total_elapsed_seconds=1500.0,
            development_elapsed_seconds=1500.0,
            current_epoch=5000.0,
        ),
    )

    assert committed.phase == commit_phase
    assert committed.dev_timebox_active is False
    assert committed.dev_timebox_consumed_seconds == 0.0
    assert committed.dev_timebox_started_at_epoch is None
    assert committed.cycle_timebox_active is True
    assert committed.cycle_timebox_consumed_seconds == 2100.0

    after_commit, _ = reduce(
        committed,
        PipelineEvent.COMMIT_SUCCESS,
        policy,
        routing_timing=RoutingTiming(
            total_elapsed_seconds=1600.0,
            development_elapsed_seconds=1500.0,
            current_epoch=5100.0,
        ),
    )
    assert after_commit.phase == analysis_phase
    assert after_commit.dev_timebox_active is False
    assert after_commit.dev_timebox_consumed_seconds == 0.0

    looped_back, _ = reduce(
        after_commit,
        AnalysisDecisionEvent(phase=analysis_phase, decision="request_changes"),
        policy,
        routing_timing=RoutingTiming(
            total_elapsed_seconds=2000.0,
            development_elapsed_seconds=0.0,
            current_epoch=7500.0,
        ),
    )

    assert looped_back.phase == dev_start
    assert looped_back.dev_timebox_active is True
    assert looped_back.dev_timebox_consumed_seconds == 0.0
    assert looped_back.dev_timebox_started_at_epoch == 7500.0


def test_rollback_via_apply_commit_outcome_preserves_iteration_reset_triple() -> None:
    policy = _policy()
    final_commit = _cycle_commit_entry(policy)
    lifecycle = policy.lifecycle_phases.get(final_commit)
    assert lifecycle is not None

    pre_final_commit = _phase_before_cycle_commit(policy)
    timings = (
        _sample_timing("development", iteration=0),
        _sample_timing("development", iteration=0),
    )

    state = PipelineState(
        phase=pre_final_commit,
        phase_timings=timings,
        cycle_timing_start_index=0,
        commit_review_rejections=1,
    )
    for reset_field in lifecycle.loop_resets:
        state = state.with_loop_iteration(reset_field, 3)

    counter_name = lifecycle.increments_counter
    assert counter_name is not None
    state = state.with_outer_progress(counter_name, 2)

    result = apply_commit_outcome(state, state, skipped=False, policy=policy)

    for reset_field in lifecycle.loop_resets:
        assert result.get_loop_iteration(reset_field) == 0

    assert result.get_outer_progress(counter_name) == 3

    assert result.cycle_timing_start_index == len(timings)

    assert result.commit_review_rejections == 1


def test_rollback_via_reducer_applies_iteration_reset_triple_and_increments_rejections() -> None:
    policy = _policy()
    final_commit = _cycle_commit_entry(policy)
    lifecycle = policy.lifecycle_phases.get(final_commit)
    assert lifecycle is not None

    pre_final_commit = _phase_before_cycle_commit(policy)
    timings = (_sample_timing("development", iteration=0),)

    state = PipelineState(
        phase=pre_final_commit,
        phase_timings=timings,
        cycle_timing_start_index=0,
        commit_review_rejections=0,
    )
    for reset_field in lifecycle.loop_resets:
        state = state.with_loop_iteration(reset_field, 2)

    counter_name = lifecycle.increments_counter
    assert counter_name is not None
    state = state.with_outer_progress(counter_name, 1)

    event = CommitReviewRejected(
        reason="Detected mass deletion",
        ref="refs/ralph/proposals/test-run/1",
        hard=True,
    )

    next_state, _ = reduce(
        state,
        event,
        policy,
        routing_timing=RoutingTiming(total_elapsed_seconds=100.0),
    )

    assert next_state.phase == "development"

    for reset_field in lifecycle.loop_resets:
        assert next_state.get_loop_iteration(reset_field) == 0
    assert next_state.get_outer_progress(counter_name) == 2
    assert next_state.cycle_timing_start_index == len(timings)

    assert next_state.commit_review_rejections == 1

    assert next_state.rollback_pending is True
    assert next_state.rollback_ref == "refs/ralph/proposals/test-run/1"
    assert next_state.last_review_rejection_reason == "Detected mass deletion"


def test_rollback_reentering_development_restarts_development_timer_from_zero() -> None:
    policy = _policy()
    assert policy.development_timebox is not None
    dev_start = policy.development_timebox.start_entry

    pre_dev_commit = _phase_before_dev_commit(policy)
    state = PipelineState(
        phase=pre_dev_commit,
        dev_timebox_active=False,
        dev_timebox_consumed_seconds=0.0,
        dev_timebox_started_at_epoch=None,
        commit_review_rejections=0,
    )

    event = CommitReviewRejected(
        reason="Harmful binary staged",
        ref="refs/ralph/proposals/run-1/0",
    )

    next_state, _ = reduce(
        state,
        event,
        policy,
        routing_timing=RoutingTiming(
            total_elapsed_seconds=1500.0,
            development_elapsed_seconds=0.0,
            current_epoch=8000.0,
        ),
    )

    assert next_state.phase == dev_start
    assert next_state.dev_timebox_active is True
    assert next_state.dev_timebox_consumed_seconds == 0.0
    assert next_state.dev_timebox_started_at_epoch == 8000.0


def test_commit_review_rejection_limit_exhaustion_transitions_to_failed_terminal() -> None:
    policy = _policy()
    pre_dev_commit = _phase_before_dev_commit(policy)

    state = PipelineState(
        phase=pre_dev_commit,
        commit_review_rejections=1,
    )

    event = CommitReviewRejected(
        reason="Repeated damage",
        ref="refs/ralph/proposals/run-1/1",
    )

    next_state, _ = reduce(
        state,
        event,
        policy,
        routing_timing=RoutingTiming(total_elapsed_seconds=500.0),
    )

    assert next_state.phase == policy.recovery.failed_route
    assert next_state.commit_review_rejections == 2
    assert next_state.rollback_pending is True
    assert next_state.rollback_ref == "refs/ralph/proposals/run-1/1"
    assert "limit reached" in (next_state.last_error or "")


def test_commit_review_rejection_after_cycle_timebox_expired_transitions_to_failed_terminal() -> (
    None
):
    policy = _policy()
    assert policy.cycle_timebox is not None
    pre_dev_commit = _phase_before_dev_commit(policy)

    state = PipelineState(
        phase=pre_dev_commit,
        cycle_timebox_active=True,
        cycle_timebox_consumed_seconds=float(policy.cycle_timebox.duration_seconds),
        commit_review_rejections=0,
    )

    event = CommitReviewRejected(
        reason="Late rejection after timebox",
        ref="refs/ralph/proposals/run-1/0",
    )

    next_state, _ = reduce(
        state,
        event,
        policy,
        routing_timing=RoutingTiming(
            total_elapsed_seconds=float(policy.cycle_timebox.duration_seconds) + 10.0
        ),
    )

    assert next_state.phase == policy.recovery.failed_route
    assert next_state.commit_review_rejections == 1
    assert next_state.rollback_pending is True
    assert "expired" in (next_state.last_error or "")


def _per_iteration_review_and_commit_phases(policy: PipelinePolicy) -> tuple[str, str]:
    for name, phase in policy.phases.items():
        if phase.role != "commit_review":
            continue
        target = phase.transitions.on_success
        target_def = policy.phases.get(target)
        if target_def is None or policy.lifecycle_phases.get(target) is not None:
            continue
        if target_def.commit_policy is not None:
            return name, target
    msg = "no per-iteration commit review phase with a commit_policy target found"
    raise AssertionError(msg)


def test_cycle_timebox_remains_active_entering_per_iteration_commit_phase() -> None:
    policy = _policy()
    pre_commit = _phase_before_dev_commit(policy)
    commit_phase = _dev_commit_entry(policy)

    state = PipelineState(
        phase=pre_commit,
        dev_timebox_active=True,
        dev_timebox_consumed_seconds=1500.0,
        dev_timebox_started_at_epoch=4000.0,
        cycle_timebox_active=True,
        cycle_timebox_consumed_seconds=2100.0,
        cycle_timebox_started_at_epoch=1000.0,
    )

    next_state, _ = reduce(
        state,
        PipelineEvent.AGENT_SUCCESS,
        policy,
        routing_timing=RoutingTiming(total_elapsed_seconds=2100.0),
    )

    assert next_state.phase == commit_phase
    assert next_state.dev_timebox_active is False
    assert next_state.dev_timebox_consumed_seconds == 0.0
    assert next_state.dev_timebox_started_at_epoch is None
    assert next_state.cycle_timebox_active is True
    assert next_state.cycle_timebox_consumed_seconds == 2100.0


def test_rollback_on_per_iteration_review_path_resets_only_the_review_loop_counter() -> None:
    policy = _policy()
    review_phase, commit_phase = _per_iteration_review_and_commit_phases(policy)
    commit_def = policy.phases[commit_phase]
    commit_policy = commit_def.commit_policy
    assert commit_policy is not None
    assert policy.lifecycle_phases.get(commit_phase) is None
    assert commit_policy.increments_counter is None

    outer_counter = _outer_budget_counter(policy)
    timings = (_sample_timing("development", iteration=0),)
    state = PipelineState(
        phase=review_phase,
        phase_timings=timings,
        cycle_timing_start_index=0,
        commit_review_rejections=0,
    )
    for reset_field in commit_policy.loop_resets:
        state = state.with_loop_iteration(reset_field, 2)
    state = state.with_outer_progress(outer_counter, 1)

    event = CommitReviewRejected(
        reason="Mass deletion detected",
        ref="refs/ralph/proposals/test-run/0",
    )

    next_state, _ = reduce(
        state,
        event,
        policy,
        routing_timing=RoutingTiming(total_elapsed_seconds=100.0),
    )

    for reset_field in commit_policy.loop_resets:
        assert next_state.get_loop_iteration(reset_field) == 0
    assert next_state.get_outer_progress(outer_counter) == 1
    assert next_state.cycle_timing_start_index == 0
    assert next_state.commit_review_rejections == 1
    assert next_state.rollback_pending is True
    assert next_state.rollback_ref == "refs/ralph/proposals/test-run/0"


def test_successful_commit_on_per_iteration_review_path_charges_the_same_accounting() -> None:
    policy = _policy()
    review_phase, commit_phase = _per_iteration_review_and_commit_phases(policy)
    commit_def = policy.phases[commit_phase]
    commit_policy = commit_def.commit_policy
    assert commit_policy is not None

    outer_counter = _outer_budget_counter(policy)
    timings = (_sample_timing("development", iteration=0),)
    state = PipelineState(
        phase=review_phase,
        phase_timings=timings,
        cycle_timing_start_index=0,
    )
    for reset_field in commit_policy.loop_resets:
        state = state.with_loop_iteration(reset_field, 2)
    state = state.with_outer_progress(outer_counter, 1)

    result = apply_commit_outcome(state, state, skipped=False, policy=policy)

    for reset_field in commit_policy.loop_resets:
        assert result.get_loop_iteration(reset_field) == 0
    assert result.get_outer_progress(outer_counter) == 1
    assert result.cycle_timing_start_index == 0
