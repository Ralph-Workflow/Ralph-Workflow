"""Single typed authority for integration actions and rebase outcome kinds.

Defines the closed vocabularies for:
- IntegrationAction: integration lifecycle progression
- RebaseOutcomeKind: the 19 outcome verbs across the pipeline
along with the single authorities `next_integration_action` and `last_outcome_reason`.
"""

from __future__ import annotations

from enum import StrEnum


class IntegrationAction(StrEnum):
    """The closed vocabulary for the integration lifecycle."""

    RESOLVE_ACTIVE_OPERATION = "resolve_active_operation"
    CONTINUE_GIT_OPERATION = "continue_git_operation"
    REINTEGRATE_AGAINST_TARGET = "reintegrate_against_target"
    LAND_TARGET = "land_target"
    PUBLISH_AFTER_LANDING = "publish_after_landing"
    WAIT_FOR_BOUNDED_RETRY = "wait_for_bounded_retry"
    REQUIRE_INTERVENTION = "require_intervention"
    COMPLETE_WITH_PROOF = "complete_with_proof"


from ralph.pipeline._rebase_outcome_kind import RebaseOutcomeKind


def _safe_attr(obj: object, name: str) -> object:
    if obj is None:
        return None
    try:
        val: object = object.__getattribute__(obj, name)
        return val
    except AttributeError:
        return None


def last_outcome_reason(record: object) -> str | None:
    """Typed accessor for the human-readable outcome reason on a record/state."""
    if record is None:
        return None
    reason: object = _safe_attr(record, "last_reason")
    if isinstance(reason, str) and reason:
        return reason
    cap_reason: object = _safe_attr(record, "repair_cap_reason")
    if isinstance(cap_reason, str) and cap_reason:
        return cap_reason
    return None


def _check_intervention(record: object) -> IntegrationAction | None:
    """Check if intervention is required."""
    diag: object = _safe_attr(record, "diagnostic_intervention_required")
    exhausted: object = _safe_attr(record, "resolution_exhausted")
    last_dec: object = _safe_attr(record, "last_decision")
    last_out: object = _safe_attr(record, "last_outcome")
    if (
        diag is True
        or exhausted is True
        or last_dec == "report_intervention_required"
        or last_out
        in (
            RebaseOutcomeKind.INTERVENTION_REQUIRED,
            RebaseOutcomeKind.DIAGNOSTIC_INTERVENTION_REQUIRED,
        )
    ):
        return IntegrationAction.REQUIRE_INTERVENTION
    return None


def _check_active_or_pending(record: object) -> IntegrationAction | None:
    """Check active operations and pending continuations or reintegrations."""
    if (
        _safe_attr(record, "resolving_rebase") is True
        or _safe_attr(record, "resolving_merge") is True
    ):
        return IntegrationAction.RESOLVE_ACTIVE_OPERATION
    if (
        _safe_attr(record, "rebase_continue_pending") is True
        or _safe_attr(record, "merge_commit_pending") is True
    ):
        return IntegrationAction.CONTINUE_GIT_OPERATION
    if _safe_attr(record, "reintegrate_pending") is True:
        return IntegrationAction.REINTEGRATE_AGAINST_TARGET
    status: object = _safe_attr(record, "phase")
    if status == "integrating":
        return IntegrationAction.REINTEGRATE_AGAINST_TARGET
    if status == "integrated":
        return IntegrationAction.LAND_TARGET
    return None


def _check_rebase_state_action(record: object, policy: object) -> IntegrationAction | None:
    """Check RebaseState outcomes, landing, and publication."""
    last_outcome: object = _safe_attr(record, "last_outcome")
    recovery_retained: object = _safe_attr(record, "recovery_record_retained")
    fast_fwd: object = _safe_attr(record, "fast_forwarded")
    if last_outcome == RebaseOutcomeKind.CONFLICT or recovery_retained is True:
        return IntegrationAction.WAIT_FOR_BOUNDED_RETRY
    if (
        last_outcome in (RebaseOutcomeKind.REBASED, RebaseOutcomeKind.MERGED)
        and fast_fwd is not True
    ):
        return IntegrationAction.LAND_TARGET
    pub_dec: object = _safe_attr(record, "last_publication_decision")
    pub_pol: object = _safe_attr(policy, "publication_policy") if policy is not None else None
    has_policy_pub: bool = (
        pub_pol is not None and fast_fwd is True and last_outcome != RebaseOutcomeKind.REMOTE_PUSHED
    )
    if (
        pub_dec
        in (
            "publish_now",
            "publishing",
            "publish_deferred_remote",
            "publish_deferred_freshness",
        )
        or has_policy_pub
    ):
        return IntegrationAction.PUBLISH_AFTER_LANDING
    return None


def next_integration_action(
    record: object,
    policy: object = None,
    clock: object = None,
) -> IntegrationAction:
    """Compute the next integration action from an integration record or rebase state.

    The single authority for integration progression decisions.
    """
    if record is None:
        return IntegrationAction.COMPLETE_WITH_PROOF

    action = _check_intervention(record)
    if action is not None:
        return action

    action = _check_active_or_pending(record)
    if action is not None:
        return action

    action = _check_rebase_state_action(record, policy)
    if action is not None:
        return action

    return IntegrationAction.COMPLETE_WITH_PROOF


__all__ = [
    "IntegrationAction",
    "RebaseOutcomeKind",
    "last_outcome_reason",
    "next_integration_action",
]
