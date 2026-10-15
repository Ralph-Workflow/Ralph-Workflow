"""Tests for IntegrationAction enum, next_integration_action authority, and drift sites."""

from ralph.pipeline._integration_action import (
    IntegrationAction,
    RebaseOutcomeKind,
    last_outcome_reason,
    next_integration_action,
)
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
    """Conflict outcome or retained recovery record yields WAIT_FOR_BOUNDED_RETRY."""
    state_conflict = RebaseState(
        last_outcome=RebaseOutcomeKind.CONFLICT,
        consecutive_conflicts=2,
    )
    assert next_integration_action(state_conflict) == IntegrationAction.WAIT_FOR_BOUNDED_RETRY

    state_retained = RebaseState(
        last_outcome=RebaseOutcomeKind.SKIPPED,
        recovery_record_retained=True,
    )
    assert next_integration_action(state_retained) == IntegrationAction.WAIT_FOR_BOUNDED_RETRY


def test_integration_action_bounded_wait_with_deadline_and_clock() -> None:
    """Bounded waiting respects injected clock and durable deadline."""
    from types import SimpleNamespace

    # Deadline not reached -> WAIT_FOR_BOUNDED_RETRY
    state_unexpired = SimpleNamespace(
        last_outcome=RebaseOutcomeKind.CONFLICT,
        recovery_record_retained=False,
        retry_deadline=100.0,
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
    )
    assert (
        next_integration_action(state_expired, clock=lambda: 105.0)
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
    """apply_conflict_budget with suppressed resolver yields WAIT_FOR_BOUNDED_RETRY."""
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
    assert next_integration_action(budgeted) == IntegrationAction.WAIT_FOR_BOUNDED_RETRY


def test_reset_case_require_intervention() -> None:
    """A failed startup check or exhausted state reaches REQUIRE_INTERVENTION."""
    state = RebaseState(
        last_outcome=RebaseOutcomeKind.DIAGNOSTIC_INTERVENTION_REQUIRED,
        last_decision="report_intervention_required",
    )
    assert next_integration_action(state) == IntegrationAction.REQUIRE_INTERVENTION
