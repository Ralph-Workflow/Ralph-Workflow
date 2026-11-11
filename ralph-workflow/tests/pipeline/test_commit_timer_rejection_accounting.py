"""Black-box pins for the commit-review rejection route and its accounting.

Split from ``test_commit_timer_characterisation.py`` so both files stay under
the repository's 1000-line file cap enforced by
:mod:`ralph.testing.audit_repo_structure`. These pins characterise the
rejection route: it crosses the commit phase the review feeds, so the frozen
timer boundaries fire exactly as they do for a commit-then-development route,
and the commit-outcome accounting is charged once through the same path a
successful commit uses. The timer modules themselves are unchanged.
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


def _sample_timing(
    phase: str,
    iteration: int = 0,
    elapsed_seconds: int = 10,
) -> PhaseTimingRecord:
    """Construct a simple PhaseTimingRecord for testing cycle timing boundaries."""
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
    """Return the (loop resets, charged counter, timing window) triple of a state.

    The three values are the whole commit-outcome accounting: the ``loop_resets``
    counters, the policy ``increments_counter`` and the
    ``cycle_timing_start_index`` that restarts the cumulative-elapsed window.
    Comparing two of these tuples compares the accounting of two routes.
    """
    return (
        tuple(state.get_loop_iteration(field) for field in reset_fields),
        state.get_outer_progress(counter_name),
        state.cycle_timing_start_index,
    )


def _rejection(
    reason: str = "Detected mass deletion",
    ref: str = "refs/ralph/proposals/test-run/0",
) -> CommitReviewRejected:
    """Return the rejection event the review phases emit on a rejected proposal."""
    return CommitReviewRejected(reason=reason, ref=ref, hard=True)


# 1. Development timebox conclusion on commit entry


def test_rejection_accounting_is_identical_to_successful_commit_accounting() -> None:
    """A rollback charges exactly the accounting a successful commit charges.

    The two routes are compared against each other instead of against a
    hand-written expectation: a successful commit at the final commit phase
    and a rejection at the commit-review phase that feeds it must produce the
    same ``loop_resets`` values, the same ``increments_counter`` charge and the
    same ``cycle_timing_start_index``. The rejection reaches that accounting
    through the same ``apply_commit_outcome`` path, because the review phase
    resolves to the commit phase it feeds.
    """
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

    # The shared accounting is the frozen triple: the resets are zeroed, the
    # counter advances by one and the cumulative-elapsed window is restarted.
    assert committed.get_loop_iteration(reset_fields[0]) == 0
    assert committed.get_outer_progress(counter_name) == 2
    assert committed.cycle_timing_start_index == len(timings)
    # The rejection counter is the one exemption: it survives and advances.
    assert rejected.commit_review_rejections == 1


def test_commit_review_rejections_counter_is_absent_from_the_lifecycle_loop_resets() -> None:
    """No lifecycle ``loop_resets`` list names the rejection counter.

    The counter is the one exemption from the commit-outcome resets, so it has
    to survive them. The per-phase ``commit_policy`` lists are pinned in
    ``tests/test_policy_validation.py``; the lifecycle group lists — the ones
    the final commit actually charges — are pinned here.
    """
    policy = _policy()
    rejection_counter = "commit_review_rejections"
    assert rejection_counter in policy.loop_counters
    assert policy.loop_counters[rejection_counter].default_max == 2

    for phase_name, lifecycle in policy.lifecycle_phases.items():
        assert rejection_counter not in lifecycle.loop_resets, (
            f"lifecycle phase {phase_name!r} unexpectedly includes {rejection_counter!r} "
            "in loop_resets"
        )


# 2. The rejection route crosses the commit entry


def test_rejection_route_crosses_the_commit_entry_and_restarts_the_development_timer() -> None:
    """The rejection route crosses the commit entry, so the development timer restarts.

    The rejection is reduced while the commit-review phase is current and the
    route leaves it through the commit phase that phase feeds, which is the
    development timebox's ``end_entry``: the entry concludes the timer
    (zeroing its consumed seconds and clearing its start epoch). The
    development window that opens after the route therefore starts from zero
    instead of resuming the seconds the pre-rejection development had already
    consumed.

    The cycle timer keeps running: the per-iteration commit phase is not the
    cycle timebox's ``end_entry``, so only the final commit phase concludes
    it.
    """
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

    # The route entered the commit phase the review feeds ...
    assert rejected.previous_phase == commit_phase
    # ... and continued to the review phase's reject target.
    assert rejected.phase == reject_target
    # The development timer concluded on the commit entry, so the development
    # window that opens after the route starts from zero.
    # RALPH-TEST-REASON: the rejection route crosses the commit entry, so the
    # development timer concludes there and the development window restarts at
    # zero instead of resuming the seconds the pre-rejection work had spent.
    assert rejected.dev_timebox_active is True
    assert rejected.dev_timebox_consumed_seconds == 0.0
    assert rejected.dev_timebox_started_at_epoch == 5100.0
    # The cycle timer is not concluded by the per-iteration commit entry.
    assert rejected.cycle_timebox_active is True
    assert rejected.cycle_timebox_consumed_seconds == 2100.0
    # The rejection is still charged as a completed commit-phase outcome.
    assert rejected.commit_review_rejections == 1
    assert rejected.rollback_pending is True
    # The reduction schedules nothing: the commit phase is entered and left
    # within it, so no commit-message agent is ever invoked.
    assert effects == []


def test_final_review_rejection_route_crosses_the_final_commit_entry_and_concludes_the_cycle_timer() -> (
    None
):
    """The final-review rejection crosses the final commit entry and ends the cycle timer.

    The final commit phase is the cycle timebox's ``end_entry``, so the route
    through it concludes the cycle timer: it goes inactive and keeps its
    consumed seconds for the run-time report. The development timer is not
    running at that point in the cycle (the per-iteration commit entry
    concluded it), so the development window that opens after the route starts
    from zero.

    The iteration budget is charged once, through the commit-outcome
    accounting of the final commit phase, and the rejection counter survives
    the resets that accounting performs.
    """
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

    # The route entered the final commit phase the review feeds ...
    assert rejected.previous_phase == final_commit
    # ... and continued to the review phase's reject target.
    assert rejected.phase == reject_target
    # The cycle timer concluded on the final commit entry and keeps its
    # consumed seconds for the run-time report.
    assert rejected.cycle_timebox_active is False
    assert rejected.cycle_timebox_consumed_seconds == 2100.0
    # The development window that opens after the route starts from zero.
    assert rejected.dev_timebox_active is True
    assert rejected.dev_timebox_consumed_seconds == 0.0
    assert rejected.dev_timebox_started_at_epoch == 5100.0
    # The commit-outcome accounting is charged once: the resets are zeroed,
    # the iteration counter advances by exactly one and the cumulative-elapsed
    # window restarts.
    for reset_field in reset_fields:
        assert rejected.get_loop_iteration(reset_field) == 0
    assert rejected.get_outer_progress(counter_name) == 2
    assert rejected.cycle_timing_start_index == len(timings)
    # The rejection counter is the one exemption from the resets.
    assert rejected.commit_review_rejections == 1


def test_rejection_route_keeps_the_rollback_marker_and_the_rejection_context() -> None:
    """The rejection route leaves the crash-safe rollback marker and its context.

    The runner's single rollback owner restores the tree from the sealed
    proposal ref once the reduction reports the rollback as pending, and it
    records the rejection reason and the ref name for the next developer
    prompt. Both live in the state the reduction hands over, so the marker,
    the ref and the reason have to survive the route.
    """
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
    # No commit was prepared for the rejected proposal.
    assert rejected.commit.agent_invoked is False


# 3. Rejection-loop timer behaviour


def test_rejection_loop_survives_the_resets_and_stops_at_the_default_limit() -> None:
    """Two consecutive rejections: the counter survives the resets and the second one ends the run.

    The rejection counter is the one exemption from the commit-outcome resets,
    so it keeps counting while the other counters are zeroed and the iteration
    budget is charged. That is what bounds the rejection loop: with the
    default limit of 2 the second consecutive rejection ends the run.
    """
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

    # The first rejection loops back to development and spends an iteration.
    assert first.phase == reject_target
    assert first.get_outer_progress(counter_name) == 1
    assert first.commit_review_rejections == 1
    # The loop_resets counters were zeroed, the rejection counter was not.
    for reset_field in lifecycle.loop_resets:
        assert first.get_loop_iteration(reset_field) == 0

    # The next iteration ends at the review phase again, with the rejection
    # counter still at one.
    second, _ = reduce(
        first.copy_with(phase=review_phase),
        CommitReviewRejected(reason="Second damage", ref="refs/ralph/proposals/test-run/1"),
        policy,
        routing_timing=RoutingTiming(total_elapsed_seconds=200.0),
    )

    # The second rejection reaches the default limit of 2 and ends the run
    # without another rollback; the rejected proposal ref is kept.
    assert second.phase == policy.recovery.failed_route
    assert second.commit_review_rejections == 2
    assert second.rollback_pending is False
    assert second.rollback_ref == "refs/ralph/proposals/test-run/1"


def test_post_timebox_rejection_fails_without_a_rollback_and_keeps_the_proposal_ref() -> None:
    """A rejection after the cycle timebox elapsed ends the run without a rollback.

    The timebox guard owns that route: the rejection never reaches the commit
    entry, so no rollback is pending and the rejected proposal ref is kept for
    manual recovery.
    """
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
