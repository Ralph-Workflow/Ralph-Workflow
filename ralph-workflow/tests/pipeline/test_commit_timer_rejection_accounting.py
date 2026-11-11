from __future__ import annotations

import functools
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.phases.commit_review import CommitReviewRejected
from ralph.phases.phase_timing_record import PhaseTimingRecord
from ralph.pipeline.cycle_timing import (
    RoutingTiming,
)
from ralph.pipeline.events import PipelineEvent
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


def _rejection(
    reason: str = "Detected mass deletion",
    ref: str = "refs/ralph/proposals/test-run/0",
) -> CommitReviewRejected:
    return CommitReviewRejected(reason=reason, ref=ref, hard=True)


def test_rejection_accounting_is_identical_to_successful_commit_accounting() -> None:
    policy = _policy()
    final_commit = _cycle_commit_entry(policy)
    review_phase = _phase_before_cycle_commit(policy)
    lifecycle = policy.lifecycle_phases.get(final_commit)
    assert lifecycle is not None
    counter_name = lifecycle.increments_counter
    assert counter_name is not None and counter_name != "none"
    reset_fields = tuple(lifecycle.loop_resets)
    assert reset_fields

    timings = (
        _sample_timing("development", iteration=0),
        _sample_timing("development", iteration=0),
        _sample_timing("development_analysis", iteration=0),
    )

    def _starting_state(phase: str) -> PipelineState:
        state = PipelineState(
            phase=phase,
            phase_timings=timings,
            cycle_timing_start_index=0,
            commit_review_rejections=0,
        )
        for reset_field in reset_fields:
            state = state.with_loop_iteration(reset_field, 3)
        return state.with_outer_progress(counter_name, 1)

    committed, _ = reduce(
        _starting_state(final_commit),
        PipelineEvent.COMMIT_SUCCESS,
        policy,
        routing_timing=RoutingTiming(total_elapsed_seconds=100.0),
    )

    rejected, _ = reduce(
        _starting_state(review_phase),
        _rejection(ref="refs/ralph/proposals/test-run/1"),
        policy,
        routing_timing=RoutingTiming(total_elapsed_seconds=100.0),
    )

    assert _commit_accounting(
        committed,
        reset_fields=reset_fields,
        counter_name=counter_name,
    ) == _commit_accounting(
        rejected,
        reset_fields=reset_fields,
        counter_name=counter_name,
    )

    assert committed.get_loop_iteration(reset_fields[0]) == 0
    assert committed.get_outer_progress(counter_name) == 2
    assert committed.cycle_timing_start_index == len(timings)
    assert rejected.commit_review_rejections == 1


def test_commit_review_rejections_counter_is_absent_from_the_lifecycle_loop_resets() -> None:
    policy = _policy()
    rejection_counter = "commit_review_rejections"
    assert rejection_counter in policy.loop_counters
    assert policy.loop_counters[rejection_counter].default_max == 2

    for phase_name, lifecycle in policy.lifecycle_phases.items():
        assert rejection_counter not in lifecycle.loop_resets, (
            f"lifecycle phase {phase_name!r} unexpectedly includes {rejection_counter!r} "
            "in loop_resets"
        )


def test_rejection_route_crosses_the_commit_entry_and_restarts_the_development_timer() -> None:
    policy = _policy()
    review_phase = _phase_before_dev_commit(policy)
    commit_phase = _dev_commit_entry(policy)
    reject_target = policy.phases[review_phase].transitions.on_reject
    assert reject_target is not None

    state = PipelineState(
        phase=review_phase,
        dev_timebox_active=True,
        dev_timebox_consumed_seconds=1500.0,
        dev_timebox_started_at_epoch=4000.0,
        cycle_timebox_active=True,
        cycle_timebox_consumed_seconds=2100.0,
        cycle_timebox_started_at_epoch=1000.0,
        phase_timings=(_sample_timing("development", iteration=0),),
        cycle_timing_start_index=0,
        commit_review_rejections=0,
    )

    rejected, effects = reduce(
        state,
        _rejection(),
        policy,
        routing_timing=RoutingTiming(
            total_elapsed_seconds=1600.0,
            development_elapsed_seconds=1500.0,
            current_epoch=5100.0,
        ),
    )

    assert rejected.previous_phase == commit_phase
    assert rejected.phase == reject_target
    assert rejected.dev_timebox_active is True
    assert rejected.dev_timebox_consumed_seconds == 0.0
    assert rejected.dev_timebox_started_at_epoch == 5100.0
    assert rejected.cycle_timebox_active is True
    assert rejected.cycle_timebox_consumed_seconds == 2100.0
    assert rejected.commit_review_rejections == 1
    assert rejected.rollback_pending is True
    assert effects == []


def test_final_review_rejection_route_crosses_the_final_commit_entry_and_concludes_the_cycle_timer() -> (
    None
):
    policy = _policy()
    review_phase = _phase_before_cycle_commit(policy)
    final_commit = _cycle_commit_entry(policy)
    reject_target = policy.phases[review_phase].transitions.on_reject
    assert reject_target is not None
    lifecycle = policy.lifecycle_phases.get(final_commit)
    assert lifecycle is not None
    counter_name = lifecycle.increments_counter
    assert counter_name is not None and counter_name != "none"
    reset_fields = tuple(lifecycle.loop_resets)
    assert reset_fields

    timings = (_sample_timing("development", iteration=0),)
    state = PipelineState(
        phase=review_phase,
        dev_timebox_active=False,
        dev_timebox_consumed_seconds=0.0,
        dev_timebox_started_at_epoch=None,
        cycle_timebox_active=True,
        cycle_timebox_consumed_seconds=2100.0,
        cycle_timebox_started_at_epoch=1000.0,
        phase_timings=timings,
        cycle_timing_start_index=0,
        commit_review_rejections=0,
    )
    for reset_field in reset_fields:
        state = state.with_loop_iteration(reset_field, 3)
    state = state.with_outer_progress(counter_name, 1)

    rejected, _ = reduce(
        state,
        _rejection(ref="refs/ralph/proposals/test-run/2"),
        policy,
        routing_timing=RoutingTiming(
            total_elapsed_seconds=1600.0,
            development_elapsed_seconds=0.0,
            current_epoch=5100.0,
        ),
    )

    assert rejected.previous_phase == final_commit
    assert rejected.phase == reject_target
    assert rejected.cycle_timebox_active is False
    assert rejected.cycle_timebox_consumed_seconds == 2100.0
    assert rejected.dev_timebox_active is True
    assert rejected.dev_timebox_consumed_seconds == 0.0
    assert rejected.dev_timebox_started_at_epoch == 5100.0
    for reset_field in reset_fields:
        assert rejected.get_loop_iteration(reset_field) == 0
    assert rejected.get_outer_progress(counter_name) == 2
    assert rejected.cycle_timing_start_index == len(timings)
    assert rejected.commit_review_rejections == 1


def test_rejection_route_keeps_the_rollback_marker_and_the_rejection_context() -> None:
    policy = _policy()
    review_phase = _phase_before_dev_commit(policy)
    ref = "refs/ralph/proposals/test-run/3"

    state = PipelineState(
        phase=review_phase,
        phase_timings=(_sample_timing("development", iteration=0),),
        commit_review_rejections=0,
    )

    rejected, _ = reduce(
        state,
        _rejection(reason="Detected mass deletion", ref=ref),
        policy,
        routing_timing=RoutingTiming(total_elapsed_seconds=100.0),
    )

    assert rejected.rollback_pending is True
    assert rejected.rollback_ref == ref
    assert rejected.last_review_rejection_reason == "Detected mass deletion"
    assert rejected.commit.agent_invoked is False


def test_rejection_loop_survives_the_resets_and_stops_at_the_default_limit() -> None:
    policy = _policy()
    final_commit = _cycle_commit_entry(policy)
    lifecycle = policy.lifecycle_phases.get(final_commit)
    assert lifecycle is not None
    counter_name = lifecycle.increments_counter
    assert counter_name is not None and counter_name != "none"
    review_phase = _phase_before_cycle_commit(policy)
    reject_target = policy.phases[review_phase].transitions.on_reject
    assert reject_target is not None

    state = PipelineState(
        phase=review_phase,
        phase_timings=(_sample_timing("development", iteration=0),),
        cycle_timing_start_index=0,
        commit_review_rejections=0,
    )
    for reset_field in lifecycle.loop_resets:
        state = state.with_loop_iteration(reset_field, 2)
    state = state.with_outer_progress(counter_name, 0)

    first, _ = reduce(
        state,
        CommitReviewRejected(reason="First damage", ref="refs/ralph/proposals/test-run/0"),
        policy,
        routing_timing=RoutingTiming(total_elapsed_seconds=100.0),
    )

    assert first.phase == reject_target
    assert first.get_outer_progress(counter_name) == 1
    assert first.commit_review_rejections == 1
    for reset_field in lifecycle.loop_resets:
        assert first.get_loop_iteration(reset_field) == 0

    second, _ = reduce(
        first.copy_with(phase=review_phase),
        CommitReviewRejected(reason="Second damage", ref="refs/ralph/proposals/test-run/1"),
        policy,
        routing_timing=RoutingTiming(total_elapsed_seconds=200.0),
    )

    assert second.phase == policy.recovery.failed_route
    assert second.commit_review_rejections == 2
    assert second.rollback_pending is False
    assert second.rollback_ref == "refs/ralph/proposals/test-run/1"


def test_post_timebox_rejection_fails_without_a_rollback_and_keeps_the_proposal_ref() -> None:
    policy = _policy()
    assert policy.cycle_timebox is not None
    review_phase = _phase_before_dev_commit(policy)
    ref = "refs/ralph/proposals/test-run/4"

    state = PipelineState(
        phase=review_phase,
        cycle_timebox_active=True,
        cycle_timebox_consumed_seconds=float(policy.cycle_timebox.duration_seconds),
        commit_review_rejections=0,
    )

    rejected, _ = reduce(
        state,
        _rejection(ref=ref),
        policy,
        routing_timing=RoutingTiming(
            total_elapsed_seconds=float(policy.cycle_timebox.duration_seconds) + 10.0
        ),
    )

    assert rejected.phase == policy.recovery.failed_route
    assert rejected.rollback_pending is False
    assert rejected.rollback_ref == ref
    assert rejected.commit_review_rejections == 1
