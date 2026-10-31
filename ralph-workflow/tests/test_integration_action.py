"""Tests for IntegrationAction enum, next_integration_action authority, and drift sites."""

import pytest

from ralph.pipeline._integration_action import (
    IntegrationAction,
    RebaseOutcomeKind,
    last_outcome_reason,
    next_integration_action,
)
from ralph.pipeline.auto_integrate_backoff import MAX_RETRY_DEADLINE_HORIZON_SECONDS
from ralph.pipeline.auto_integrate_conflict_budget import (
    ConflictIdentity,
    apply_conflict_budget,
)
from ralph.pipeline.auto_integrate_record import IntegrationRecord
from ralph.pipeline.rebase_state import RebaseState


def test_integration_action_resolve_active_operation() -> None:
    """Active rebase or merge resolution yields RESOLVE_ACTIVE_OPERATION."""
    record = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha="abc111",
        pre_target_sha="def222",
        resolving_rebase=True,
    )
    assert next_integration_action(record) == IntegrationAction.RESOLVE_ACTIVE_OPERATION

    state = RebaseState(resolving_merge=True)
    assert next_integration_action(state) == IntegrationAction.RESOLVE_ACTIVE_OPERATION


def test_integration_action_continue_git_operation() -> None:
    """Pending rebase continue or merge commit yields CONTINUE_GIT_OPERATION."""
    record_rebase = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha="abc111",
        pre_target_sha="def222",
        rebase_continue_pending=True,
    )
    assert next_integration_action(record_rebase) == IntegrationAction.CONTINUE_GIT_OPERATION

    record_merge = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha="abc111",
        pre_target_sha="def222",
        merge_commit_pending=True,
    )
    assert next_integration_action(record_merge) == IntegrationAction.CONTINUE_GIT_OPERATION


def test_integration_action_continue_over_resolve_when_overlap() -> None:
    """Continuation pending wins when both resolving_rebase and rebase_continue_pending are set."""
    record = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha="abc111",
        pre_target_sha="def222",
        resolving_rebase=True,
        rebase_continue_pending=True,
    )
    assert next_integration_action(record) == IntegrationAction.CONTINUE_GIT_OPERATION


def test_integration_action_merge_tree_only_yields_continue_git_operation() -> None:
    """Prepared merge tree with no pending flags routes to continuation."""
    record = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha="abc111",
        pre_target_sha="def222",
        merge_commit_tree="tree999",
    )
    assert next_integration_action(record) == IntegrationAction.CONTINUE_GIT_OPERATION


def test_integration_action_reintegrate_against_target() -> None:
    """Record in integrating phase or with reintegrate_pending yields REINTEGRATE_AGAINST_TARGET."""
    record = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha="abc111",
        pre_target_sha="def222",
    )
    assert next_integration_action(record) == IntegrationAction.REINTEGRATE_AGAINST_TARGET

    record_pending = IntegrationRecord(
        phase="integrated",
        target="main",
        pre_feature_sha="abc111",
        pre_target_sha="def222",
        reintegrate_pending=True,
    )
    assert next_integration_action(record_pending) == IntegrationAction.REINTEGRATE_AGAINST_TARGET


def test_integration_action_land_target() -> None:
    """Integrated record or unlanded rebase/merge state yields LAND_TARGET."""
    record = IntegrationRecord(
        phase="integrated",
        target="main",
        pre_feature_sha="abc111",
        pre_target_sha="def222",
        integrated_feature_sha="feat333",
    )
    assert next_integration_action(record) == IntegrationAction.LAND_TARGET

    state = RebaseState(
        last_outcome=RebaseOutcomeKind.REBASED,
        fast_forwarded=False,
    )
    assert next_integration_action(state) == IntegrationAction.LAND_TARGET


def test_integration_action_publish_after_landing() -> None:
    """Landed state with pending publication decision yields PUBLISH_AFTER_LANDING."""
    state = RebaseState(
        last_outcome=RebaseOutcomeKind.PUBLISHING,
        fast_forwarded=True,
    )
    assert next_integration_action(state) == IntegrationAction.PUBLISH_AFTER_LANDING

    from types import SimpleNamespace

    policy = SimpleNamespace(publication_policy="remote_push")
    landed_state = RebaseState(
        last_outcome=RebaseOutcomeKind.FAST_FORWARDED,
        fast_forwarded=True,
    )
    assert (
        next_integration_action(landed_state, policy=policy)
        == IntegrationAction.PUBLISH_AFTER_LANDING
    )


def test_integration_action_wait_for_bounded_retry() -> None:
    """Conflict outcome or retained recovery record yields WAIT_FOR_BOUNDED_RETRY.

    A typed ``retry_deadline`` is required -- the action authority
    fails closed to ``REQUIRE_INTERVENTION`` when the deadline is
    missing or malformed, so this test arms both the conflict and
    the retained-recovery cases with a future deadline (injected
    via the clock so the test is independent of process monotonic
    time).
    """
    state_conflict = RebaseState(
        last_outcome=RebaseOutcomeKind.CONFLICT,
        consecutive_conflicts=2,
        retry_deadline=100.0,
    )
    assert (
        next_integration_action(state_conflict, clock=lambda: 50.0)
        == IntegrationAction.WAIT_FOR_BOUNDED_RETRY
    )

    state_retained = RebaseState(
        last_outcome=RebaseOutcomeKind.SKIPPED,
        recovery_record_retained=True,
        retry_deadline=100.0,
    )
    assert (
        next_integration_action(state_retained, clock=lambda: 50.0)
        == IntegrationAction.WAIT_FOR_BOUNDED_RETRY
    )


def test_integration_action_bounded_wait_with_deadline_and_clock() -> None:
    """Bounded waiting respects injected clock and the typed ``retry_deadline`` field."""
    from types import SimpleNamespace

    # Deadline not reached -> WAIT_FOR_BOUNDED_RETRY
    state_unexpired = SimpleNamespace(
        last_outcome=RebaseOutcomeKind.CONFLICT,
        recovery_record_retained=False,
        retry_deadline=100.0,
        fast_forwarded=False,
    )
    assert (
        next_integration_action(state_unexpired, clock=lambda: 50.0)
        == IntegrationAction.WAIT_FOR_BOUNDED_RETRY
    )

    # Deadline reached or exceeded -> REQUIRE_INTERVENTION
    state_expired = SimpleNamespace(
        last_outcome=RebaseOutcomeKind.CONFLICT,
        recovery_record_retained=False,
        retry_deadline=100.0,
        fast_forwarded=False,
    )
    assert (
        next_integration_action(state_expired, clock=lambda: 105.0)
        == IntegrationAction.REQUIRE_INTERVENTION
    )

    # Missing retry_deadline on a conflict record fails closed to
    # ``REQUIRE_INTERVENTION`` -- the architecture forbids an
    # unbounded wait.
    state_no_deadline = SimpleNamespace(
        last_outcome=RebaseOutcomeKind.CONFLICT,
        recovery_record_retained=False,
        retry_deadline=None,
        fast_forwarded=False,
    )
    assert (
        next_integration_action(state_no_deadline, clock=lambda: 50.0)
        == IntegrationAction.REQUIRE_INTERVENTION
    )


def test_integration_action_require_intervention() -> None:
    """Exhausted resolution or diagnostic intervention yields REQUIRE_INTERVENTION."""
    record = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha="abc111",
        pre_target_sha="def222",
        diagnostic_intervention_required=True,
    )
    assert next_integration_action(record) == IntegrationAction.REQUIRE_INTERVENTION

    state_exhausted = RebaseState(
        last_outcome=RebaseOutcomeKind.CONFLICT,
        resolution_exhausted=True,
    )
    assert next_integration_action(state_exhausted) == IntegrationAction.REQUIRE_INTERVENTION

    state_intervene = RebaseState(
        last_outcome=RebaseOutcomeKind.INTERVENTION_REQUIRED,
    )
    assert next_integration_action(state_intervene) == IntegrationAction.REQUIRE_INTERVENTION


def test_integration_action_complete_with_proof() -> None:
    """Positive proof or cleanly closed terminal state yields COMPLETE_WITH_PROOF."""
    assert next_integration_action(None, proof=True) == IntegrationAction.COMPLETE_WITH_PROOF

    state_closed = RebaseState(
        last_outcome=RebaseOutcomeKind.REMOTE_PUSHED,
        fast_forwarded=True,
    )
    assert next_integration_action(state_closed) == IntegrationAction.COMPLETE_WITH_PROOF


def test_integration_action_missing_or_unmatched_without_proof_requires_intervention() -> None:
    """Missing or unmatched input without proof returns REQUIRE_INTERVENTION."""
    assert next_integration_action(None) == IntegrationAction.REQUIRE_INTERVENTION
    assert next_integration_action(RebaseState()) == IntegrationAction.REQUIRE_INTERVENTION


def test_projection_fields_do_not_feed_into_control_flow() -> None:
    """Projection fields like last_decision and last_publication_decision do not affect decisions."""
    # last_decision alone should not trigger REQUIRE_INTERVENTION without durable facts
    state_proj_dec = RebaseState(
        last_decision="report_intervention_required",
        fast_forwarded=True,
        last_outcome=RebaseOutcomeKind.FAST_FORWARDED,
    )
    assert next_integration_action(state_proj_dec) == IntegrationAction.COMPLETE_WITH_PROOF

    # last_publication_decision alone should not trigger PUBLISH_AFTER_LANDING
    state_proj_pub = RebaseState(
        last_publication_decision="publish_now",
        fast_forwarded=True,
        last_outcome=RebaseOutcomeKind.FAST_FORWARDED,
    )
    assert next_integration_action(state_proj_pub) == IntegrationAction.COMPLETE_WITH_PROOF


def test_last_outcome_reason_accessor() -> None:
    """last_outcome_reason retrieves reason from record or state."""
    state = RebaseState(last_reason="no commits beyond target")
    assert last_outcome_reason(state) == "no commits beyond target"
    assert last_outcome_reason(None) is None


def test_startup_case_predicate() -> None:
    """Startup checkpoint save exercises typed last_outcome != SKIPPED."""
    startup_rebase = RebaseState(last_outcome=RebaseOutcomeKind.REBASED)
    assert startup_rebase.last_outcome != RebaseOutcomeKind.SKIPPED

    startup_skipped = RebaseState(last_outcome=RebaseOutcomeKind.SKIPPED)
    assert startup_skipped.last_outcome == RebaseOutcomeKind.SKIPPED


def test_conflict_budget_case() -> None:
    """apply_conflict_budget with suppressed resolver yields WAIT_FOR_BOUNDED_RETRY.

    The action authority requires a typed ``retry_deadline``; the
    budget owner is responsible for arming it before yielding a
    bounded wait. Without the deadline, an unresolved conflict
    routes to ``REQUIRE_INTERVENTION`` -- the action authority does
    not invent an unbounded wait.
    """
    identity = ConflictIdentity(
        feature_sha="feat123",
        target_sha="tgt456",
        conflicted_paths=("file.py",),
        stage_oids=(),
        scope="",
    )
    prior = RebaseState(consecutive_conflicts=0)
    current = RebaseState(
        last_outcome=RebaseOutcomeKind.CONFLICT,
        last_target="main",
        fast_forwarded=False,
    )
    budgeted = apply_conflict_budget(
        current,
        prior=prior,
        target="main",
        identity=identity,
        resolver_suppressed=True,
    )
    assert budgeted.consecutive_conflicts == 1
    assert "budget exhausted" in (budgeted.last_reason or "")

    # Without a typed deadline the action authority fails closed.
    assert next_integration_action(budgeted) == IntegrationAction.REQUIRE_INTERVENTION

    # With a future typed deadline the action authority honors the
    # bounded wait (injected clock keeps the test independent of
    # process monotonic time).
    budgeted_armed = budgeted.model_copy(update={"retry_deadline": 100.0})
    assert (
        next_integration_action(budgeted_armed, clock=lambda: 50.0)
        == IntegrationAction.WAIT_FOR_BOUNDED_RETRY
    )


def test_reset_case_require_intervention() -> None:
    """A failed startup check or exhausted state reaches REQUIRE_INTERVENTION."""
    state = RebaseState(
        last_outcome=RebaseOutcomeKind.DIAGNOSTIC_INTERVENTION_REQUIRED,
        last_decision="report_intervention_required",
    )
    assert next_integration_action(state) == IntegrationAction.REQUIRE_INTERVENTION


def test_target_movement_yields_reintegrate_against_target_with_ownership_kept() -> None:
    """Target movement yields REINTEGRATE_AGAINST_TARGET with ownership kept."""
    from types import SimpleNamespace

    # Integrating phase with target movement / reintegrate_pending
    record_integrating = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha="abc111",
        pre_target_sha="def222",
        reintegrate_pending=True,
        owning_worktree="/wt/alpha",
    )
    assert (
        next_integration_action(record_integrating) == IntegrationAction.REINTEGRATE_AGAINST_TARGET
    )
    assert record_integrating.owning_worktree == "/wt/alpha"

    # Integrated phase with target movement / reintegrate_pending
    record_integrated = IntegrationRecord(
        phase="integrated",
        target="main",
        pre_feature_sha="abc111",
        pre_target_sha="def222",
        reintegrate_pending=True,
        owning_worktree="/wt/beta",
    )
    assert (
        next_integration_action(record_integrated) == IntegrationAction.REINTEGRATE_AGAINST_TARGET
    )
    assert record_integrated.owning_worktree == "/wt/beta"

    # target_moved attribute yields REINTEGRATE_AGAINST_TARGET
    target_moved_obj = SimpleNamespace(target_moved=True, owning_worktree="/wt/gamma")
    assert next_integration_action(target_moved_obj) == IntegrationAction.REINTEGRATE_AGAINST_TARGET

    # RebaseState with INTEGRATING outcome yields REINTEGRATE_AGAINST_TARGET
    state_integrating = RebaseState(last_outcome=RebaseOutcomeKind.INTEGRATING)
    assert (
        next_integration_action(state_integrating) == IntegrationAction.REINTEGRATE_AGAINST_TARGET
    )


def test_completed_conflict_resolution_with_landing_pending_yields_land_target() -> None:
    """Completed conflict resolution with landing pending yields LAND_TARGET."""
    from types import SimpleNamespace

    # Integrated record (completed conflict resolution, fast-forward pending)
    record = IntegrationRecord(
        phase="integrated",
        target="main",
        pre_feature_sha="abc111",
        pre_target_sha="def222",
        integrated_feature_sha="head333",
        reintegrate_pending=False,
    )
    assert next_integration_action(record) == IntegrationAction.LAND_TARGET

    # Integrated record with landing pending (integrated_feature_sha is None)
    record_landing_pending = IntegrationRecord(
        phase="integrated",
        target="main",
        pre_feature_sha="abc111",
        pre_target_sha="def222",
        integrated_feature_sha=None,
        reintegrate_pending=False,
    )
    assert next_integration_action(record_landing_pending) == IntegrationAction.LAND_TARGET

    # Object with landing_pending attribute yields LAND_TARGET
    landing_obj = SimpleNamespace(landing_pending=True)
    assert next_integration_action(landing_obj) == IntegrationAction.LAND_TARGET

    # RebaseState with LANDING outcome and unlanded yields LAND_TARGET
    state_landing = RebaseState(
        last_outcome=RebaseOutcomeKind.LANDING,
        fast_forwarded=False,
    )
    assert next_integration_action(state_landing) == IntegrationAction.LAND_TARGET

    # RebaseState with REBASED / MERGED outcome and unlanded yields LAND_TARGET
    state_rebased = RebaseState(
        last_outcome=RebaseOutcomeKind.REBASED,
        fast_forwarded=False,
    )
    assert next_integration_action(state_rebased) == IntegrationAction.LAND_TARGET

    state_merged = RebaseState(
        last_outcome=RebaseOutcomeKind.MERGED,
        fast_forwarded=False,
    )
    assert next_integration_action(state_merged) == IntegrationAction.LAND_TARGET


def test_no_development_reentry_on_target_movement_and_landing_pending() -> None:
    """Target movement and pending landing block recovery without development re-entry."""
    from ralph.pipeline._pipeline_state_machine import PhaseDecision, decide_phase_transition
    from ralph.pipeline.events import PipelineEvent
    from ralph.pipeline.reducer import reduce
    from ralph.pipeline.state import PipelineState
    from ralph.policy.models import PhaseDefinition, PhaseTransition, PipelinePolicy

    policy = PipelinePolicy(
        phases={
            "development": PhaseDefinition(
                drain="development",
                transitions=PhaseTransition(
                    on_success="complete",
                    on_loopback="development",
                ),
            ),
            "integrating": PhaseDefinition(
                drain="development",
                transitions=PhaseTransition(
                    on_success="complete",
                    on_loopback="integrating",
                ),
            ),
        },
        entry_phase="development",
        terminal_phase="complete",
    )

    # 1. Target movement: reintegrate_pending=True in integrating phase blocks and avoids dev re-entry
    record_moved = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha="abc111",
        pre_target_sha="def222",
        reintegrate_pending=True,
    )
    state = PipelineState(phase="integrating", rebase=RebaseState())
    decision = decide_phase_transition(
        state,
        policy,
        integration_record=record_moved,
        rebase_state=state.rebase,
    )
    assert decision == PhaseDecision.BLOCK_FOR_RECOVERY

    new_state, _ = reduce(
        state, PipelineEvent.AGENT_SUCCESS, policy, integration_record=record_moved
    )
    assert new_state.phase == "integrating"
    assert new_state.phase != "development"
    assert new_state.rebase.last_decision == PhaseDecision.BLOCK_FOR_RECOVERY.value

    # 2. Completed conflict resolution with landing pending blocks and avoids dev re-entry
    record_landing = IntegrationRecord(
        phase="integrated",
        target="main",
        pre_feature_sha="abc111",
        pre_target_sha="def222",
        reintegrate_pending=False,
    )
    decision_landing = decide_phase_transition(
        state,
        policy,
        integration_record=record_landing,
        rebase_state=state.rebase,
    )
    assert decision_landing == PhaseDecision.BLOCK_FOR_RECOVERY

    new_state_landing, _ = reduce(
        state, PipelineEvent.AGENT_SUCCESS, policy, integration_record=record_landing
    )
    assert new_state_landing.phase == "integrating"
    assert new_state_landing.phase != "development"
    assert new_state_landing.rebase.last_decision == PhaseDecision.BLOCK_FOR_RECOVERY.value


def test_transition_field_map_updates_for_reintegrate_pending_and_ownership() -> None:
    """Transition table allows reintegrate_pending and owning_worktree updates."""
    from ralph.pipeline.auto_integrate_record import (
        MARK_REINTEGRATE_PENDING,
        PROMOTE_TO_INTEGRATED,
        START_INTEGRATING,
    )

    record = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha="abc111",
        pre_target_sha="def222",
        reintegrate_pending=True,
        owning_worktree="/wt/initial",
    )

    # MARK_REINTEGRATE_PENDING can update/preserve owning_worktree
    retained = record.with_transition(
        MARK_REINTEGRATE_PENDING,
        reintegrate_pending=True,
        owning_worktree="/wt/updated",
    )
    assert retained.reintegrate_pending is True
    assert retained.owning_worktree == "/wt/updated"

    # START_INTEGRATING can clear or set reintegrate_pending
    restarted = retained.with_transition(
        START_INTEGRATING,
        phase="integrating",
        target="main",
        pre_feature_sha="abc111",
        pre_target_sha="def333",
        reintegrate_pending=False,
    )
    assert restarted.reintegrate_pending is False

    # PROMOTE_TO_INTEGRATED can clear reintegrate_pending
    promoted = record.with_transition(
        PROMOTE_TO_INTEGRATED,
        phase="integrated",
        integrated_feature_sha="feat999",
        reintegrate_pending=False,
    )
    assert promoted.phase == "integrated"
    assert promoted.reintegrate_pending is False


def test_conflict_deadline_from_a_previous_boot_does_not_wait() -> None:
    """A checkpoint written before a reboot no longer reads as a bounded wait.

    ``retry_deadline`` is a persisted MONOTONIC instant. The checkpoint was
    written after five days of uptime (deadline = 432 002 s); after the
    reboot the monotonic clock reads 30 s. The JSON shape is unchanged, so
    the old checkpoint still loads, but a deadline ~5 days ahead -- further
    than any writer arms -- is a previous boot's reading and has expired.
    Before the fix this projected ``WAIT_FOR_BOUNDED_RETRY`` for five days.
    """
    restored = RebaseState.model_validate(
        {"last_outcome": "conflict", "retry_deadline": 5 * 24 * 3600.0 + 2.0}
    )

    assert restored.retry_deadline == 5 * 24 * 3600.0 + 2.0
    assert (
        next_integration_action(restored, clock=lambda: 30.0)
        is IntegrationAction.REQUIRE_INTERVENTION
    )


@pytest.mark.parametrize(
    ("remaining", "action"),
    [
        (2.0, IntegrationAction.WAIT_FOR_BOUNDED_RETRY),
        (MAX_RETRY_DEADLINE_HORIZON_SECONDS, IntegrationAction.WAIT_FOR_BOUNDED_RETRY),
        (MAX_RETRY_DEADLINE_HORIZON_SECONDS + 0.5, IntegrationAction.REQUIRE_INTERVENTION),
        (0.0, IntegrationAction.REQUIRE_INTERVENTION),
    ],
)
def test_conflict_deadline_waits_only_within_the_retry_horizon(
    remaining: float, action: IntegrationAction
) -> None:
    """The bounded wait holds only for a deadline within the writers' horizon."""
    state = RebaseState(last_outcome=RebaseOutcomeKind.CONFLICT, retry_deadline=1_000.0 + remaining)

    assert next_integration_action(state, clock=lambda: 1_000.0) is action
