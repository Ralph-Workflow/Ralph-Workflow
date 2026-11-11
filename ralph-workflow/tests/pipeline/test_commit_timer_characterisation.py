"""Black-box characterisation tests for timer behaviour around commit phases.

These tests pin the frozen timer and progress behaviour around commit entry and
commit outcome across the transition from legacy commit phases to future
pipeline phases, without relying on phase-name string literals.
"""

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

_DEFAULTS_DIR = Path(__file__).resolve().parents[2] / "ralph" / "policy" / "defaults"


@functools.cache
def _policy() -> PipelinePolicy:
    return load_policy(_DEFAULTS_DIR).pipeline


def _dev_commit_entry(policy: PipelinePolicy) -> str:
    """Return the configured commit boundary phase for development timebox."""
    assert policy.development_timebox is not None
    return policy.development_timebox.end_entry


def _cycle_commit_entry(policy: PipelinePolicy) -> str:
    """Return the configured commit boundary phase for cycle timebox."""
    assert policy.cycle_timebox is not None
    return policy.cycle_timebox.end_entry


def _phase_before_dev_commit(policy: PipelinePolicy) -> str:
    """Resolve the phase that transitions on success to the dev commit phase."""
    target = _dev_commit_entry(policy)
    for name, phase in policy.phases.items():
        if phase.transitions.on_success == target:
            return name
    msg = f"No phase found that transitions on success to {target!r}"
    raise AssertionError(msg)


def _phase_before_cycle_commit(policy: PipelinePolicy) -> str:
    """Resolve the phase that transitions on success to the cycle final commit phase."""
    target = _cycle_commit_entry(policy)
    for name, phase in policy.phases.items():
        if phase.transitions.on_success == target:
            return name
    msg = f"No phase found that transitions on success to {target!r}"
    raise AssertionError(msg)


def _sample_timing(phase: str, iteration: int = 0) -> PhaseTimingRecord:
    """Construct a simple PhaseTimingRecord for testing cycle timing boundaries."""
    return PhaseTimingRecord(
        phase=phase,
        iteration=iteration,
        started_at=100.0,
        elapsed=timedelta(seconds=10),
        elapsed_seconds=10,
    )


def _outer_budget_counter(policy: PipelinePolicy) -> str:
    """Return the tracked budget counter charged by the commit phases.

    The name is read from the policy so this characterisation stays
    name-neutral.
    """
    for name, counter in policy.budget_counters.items():
        if counter.tracks_budget:
            return name
    msg = "policy declares no tracked budget counter"
    raise AssertionError(msg)


# ---------------------------------------------------------------------------
# 1. Development timebox conclusion on commit entry
# ---------------------------------------------------------------------------


def test_development_timebox_concludes_on_entry_to_commit_phase_via_reducer() -> None:
    """Entering the commit phase concludes the dev timebox (active=False, zeroed, epoch cleared)."""
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
    """conclude_development_timebox_on_route_out_of_development only concludes for end_entry."""
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
    """Transition from development to the pre-commit phase preserves active development timer."""
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


# ---------------------------------------------------------------------------
# 2. Cycle timebox conclusion on commit entry
# ---------------------------------------------------------------------------


def test_cycle_timebox_concludes_on_entry_to_final_commit_via_reducer() -> None:
    """Entering the final commit phase concludes cycle timing but preserves consumed seconds."""
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
    """conclude_cycle_on_route_out_of_cycle only concludes on entry to ct.end_entry."""
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
    """Routing from analysis to pre-final-commit phase keeps the cycle timer active."""
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


# ---------------------------------------------------------------------------
# 3. Commit completion loop_resets / iteration increment / cycle_timing_start_index
# ---------------------------------------------------------------------------


def test_commit_completion_resets_lifecycle_phase_counters_and_timing_window() -> None:
    """Commit completion on a lifecycle phase zeroes loop_resets, advances counter, sets cycle_timing_start_index."""
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

    # 1. All loop_resets declared for the lifecycle phase are zeroed
    for reset_field in lifecycle.loop_resets:
        assert result.get_loop_iteration(reset_field) == 0

    # 2. Outer progress counter advances by 1
    assert result.get_outer_progress(counter_name) == 3

    # 3. cycle_timing_start_index advances to len(phase_timings)
    assert result.cycle_timing_start_index == len(timings)


def test_commit_completion_via_reducer_applies_lifecycle_resets() -> None:
    """Reducer handling of COMMIT_SUCCESS runs apply_commit_outcome for lifecycle commit."""
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
    """Commit completion on an intermediate commit phase zeroes loop_resets from commit_policy."""
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

    # Intermediate commit does not alter cycle_timing_start_index
    assert result.cycle_timing_start_index == 0


# ---------------------------------------------------------------------------
# 4. Commit-then-development restarts the dev timer
# ---------------------------------------------------------------------------


def test_commit_then_development_restarts_development_timer_from_zero() -> None:
    """Concluded dev timer restarts from zero when re-entering development from commit/analysis."""
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
    """apply_development_timebox starts an inactive timer on target_phase == dt.start_entry."""
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
    """Re-entering development while dev timer is already active does not zero consumed seconds."""
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


# ---------------------------------------------------------------------------
# 5. Rollback preserves the iteration reset triple and commit_review_rejections survives
# ---------------------------------------------------------------------------


def test_rollback_via_apply_commit_outcome_preserves_iteration_reset_triple() -> None:
    """apply_commit_outcome for a review phase or its commit phase zeroes loop_resets, advances counter, sets cycle_timing_start_index."""
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

    # 1. All loop_resets are zeroed
    for reset_field in lifecycle.loop_resets:
        assert result.get_loop_iteration(reset_field) == 0

    # 2. Outer progress counter advances by 1
    assert result.get_outer_progress(counter_name) == 3

    # 3. cycle_timing_start_index advances to len(phase_timings)
    assert result.cycle_timing_start_index == len(timings)

    # 4. commit_review_rejections survives iteration resets
    assert result.commit_review_rejections == 1


def test_rollback_via_reducer_applies_iteration_reset_triple_and_increments_rejections() -> None:
    """Reducer handling of CommitReviewRejected runs apply_commit_outcome and preserves rejections counter."""
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

    # 1. Routes to development (on_reject)
    assert next_state.phase == "development"

    # 2. Iteration reset triple preserved:
    for reset_field in lifecycle.loop_resets:
        assert next_state.get_loop_iteration(reset_field) == 0
    assert next_state.get_outer_progress(counter_name) == 2
    assert next_state.cycle_timing_start_index == len(timings)

    # 3. commit_review_rejections counter survived reset AND incremented
    assert next_state.commit_review_rejections == 1

    # 4. Rollback pending is flagged for crash safety
    assert next_state.rollback_pending is True
    assert next_state.rollback_ref == "refs/ralph/proposals/test-run/1"
    assert next_state.last_review_rejection_reason == "Detected mass deletion"


def test_rollback_reentering_development_restarts_development_timer_from_zero() -> None:
    """Rollback returning to development restarts concluded development timer from zero."""
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
    """When commit_review_rejections reaches the limit (default 2), fail to failed_terminal.

    The run fails once the counter REACHES its limit: with the default limit of
    2 the second consecutive rejection is terminal. The terminal transition
    performs no rollback and keeps the rejected proposal ref.
    """
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
    # RALPH-TEST-REASON: Rejection limit is now >= policy limit (reaches 2) instead of > 2
    assert next_state.commit_review_rejections == 2
    assert next_state.rollback_pending is False
    # The rejected proposal ref is kept, so the rejected work stays recoverable.
    assert next_state.rollback_ref == "refs/ralph/proposals/run-1/1"
    assert "limit reached" in (next_state.last_error or "")


def test_commit_review_rejection_after_cycle_timebox_expired_transitions_to_failed_terminal() -> (
    None
):
    """When cycle timebox has expired, rejection routes to failed_terminal rather than development."""
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
    assert next_state.rollback_pending is False
    assert "expired" in (next_state.last_error or "")


def _per_iteration_review_and_commit_phases(policy: PipelinePolicy) -> tuple[str, str]:
    """Return the per-iteration review phase and the commit phase it feeds.

    The per-iteration review path is the one whose commit phase is not a
    lifecycle phase and declares its own ``commit_policy``. It is resolved
    from the policy so this characterisation stays name-neutral.
    """
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
    """Entering the per-iteration commit phase concludes only the development timer.

    The cycle timer is concluded at its own ``end_entry`` (the final commit
    phase), so the per-iteration commit boundary leaves it running with its
    consumed seconds intact.
    """
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
    # The development timer concludes at this boundary ...
    assert next_state.dev_timebox_active is False
    assert next_state.dev_timebox_consumed_seconds == 0.0
    assert next_state.dev_timebox_started_at_epoch is None
    # ... while the cycle timer keeps running until its own end_entry.
    assert next_state.cycle_timebox_active is True
    assert next_state.cycle_timebox_consumed_seconds == 2100.0


def test_rollback_on_per_iteration_review_path_resets_only_the_review_loop_counter() -> None:
    """A rejection on the per-iteration review path resets the review loop counter only.

    The per-iteration commit phase is not a lifecycle phase and declares no
    ``increments_counter``, so the rollback charges no budget counter and does
    not move ``cycle_timing_start_index`` — the very accounting a successful
    commit on that path performs. ``commit_review_rejections`` still survives
    the reset and is incremented.
    """
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

    # The loop counter declared by the fed commit phase is reset.
    for reset_field in commit_policy.loop_resets:
        assert next_state.get_loop_iteration(reset_field) == 0
    # No budget counter is charged on this path.
    assert next_state.get_outer_progress(outer_counter) == 1
    # The cumulative-elapsed window is not restarted on this path.
    assert next_state.cycle_timing_start_index == 0
    # The rejection counter survives the reset and is incremented.
    assert next_state.commit_review_rejections == 1
    assert next_state.rollback_pending is True
    assert next_state.rollback_ref == "refs/ralph/proposals/test-run/0"


def test_successful_commit_on_per_iteration_review_path_charges_the_same_accounting() -> None:
    """A successful commit on the per-iteration path charges exactly the same accounting.

    The rollback and the commit are the same accounting path, so the rejection
    characterisation above is identical to the commit characterisation.
    """
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
