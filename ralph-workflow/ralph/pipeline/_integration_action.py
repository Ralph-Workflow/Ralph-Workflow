"""Single typed authority for integration actions and rebase outcome kinds.

Defines the closed vocabularies for:
- IntegrationAction: integration lifecycle progression
- RebaseOutcomeKind: the 19 outcome verbs across the pipeline
along with the single authorities `next_integration_action` and `last_outcome_reason`.
"""

from __future__ import annotations

import time
from enum import StrEnum

from ralph.pipeline._rebase_outcome_kind import RebaseOutcomeKind


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
    """Check if intervention is required from current durable facts only."""
    diag: object = _safe_attr(record, "diagnostic_intervention_required")
    exhausted: object = _safe_attr(record, "resolution_exhausted")
    last_out: object = _safe_attr(record, "last_outcome")
    if (
        diag is True
        or exhausted is True
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
    if _safe_attr(record, "is_integrating") is True or _safe_attr(record, "phase") == "integrating":
        return IntegrationAction.REINTEGRATE_AGAINST_TARGET
    if _safe_attr(record, "is_integrated") is True or _safe_attr(record, "phase") == "integrated":
        return IntegrationAction.LAND_TARGET
    return None


def _current_time(clock: object) -> float | None:
    if callable(clock):
        try:
            val: object = clock()
            if isinstance(val, (int, float)):
                return float(val)
            if isinstance(val, str):
                return float(val)
            return None
        except Exception:
            return None
    if isinstance(clock, (int, float)):
        return float(clock)
    return None


def _check_rebase_state_action(
    record: object,
    policy: object,
    clock: object = None,
) -> IntegrationAction | None:
    """Check RebaseState outcomes, landing, and publication."""
    last_outcome: object = _safe_attr(record, "last_outcome")
    recovery_retained: object = _safe_attr(record, "recovery_record_retained")
    fast_fwd: object = _safe_attr(record, "fast_forwarded")
    if last_outcome == RebaseOutcomeKind.CONFLICT or recovery_retained is True:
        deadline = _safe_attr(record, "retry_deadline")
        if deadline is None:
            deadline = _safe_attr(record, "deadline")
        if deadline is not None and isinstance(deadline, (int, float, str)):
            try:
                deadline_val = float(deadline)
                now = _current_time(clock)
                if now is None:
                    now = time.monotonic()
                if now >= deadline_val:
                    return IntegrationAction.REQUIRE_INTERVENTION
            except (ValueError, TypeError):
                pass
        return IntegrationAction.WAIT_FOR_BOUNDED_RETRY
    if (
        last_outcome in (RebaseOutcomeKind.REBASED, RebaseOutcomeKind.MERGED)
        and fast_fwd is not True
    ):
        return IntegrationAction.LAND_TARGET
    pub_pol: object = _safe_attr(policy, "publication_policy") if policy is not None else None
    has_policy_pub: bool = (
        pub_pol is not None and fast_fwd is True and last_outcome != RebaseOutcomeKind.REMOTE_PUSHED
    )
    if last_outcome == RebaseOutcomeKind.PUBLISHING or has_policy_pub:
        return IntegrationAction.PUBLISH_AFTER_LANDING
    return None


def _has_completion_proof(
    record: object,
    policy: object = None,
    proof: object = None,
) -> bool:
    """Check whether positive proof of integration completion is present."""
    if proof is True or (proof is not None and proof is not False):
        return True
    if _safe_attr(record, "completion_proof") is True:
        return True
    if _safe_attr(record, "proof") is not None and _safe_attr(record, "proof") is not False:
        return True
    last_outcome = _safe_attr(record, "last_outcome")
    fast_fwd = _safe_attr(record, "fast_forwarded")
    pub_pol = _safe_attr(policy, "publication_policy") if policy is not None else None
    has_policy_pub = (
        pub_pol is not None and fast_fwd is True and last_outcome != RebaseOutcomeKind.REMOTE_PUSHED
    )
    is_landed_terminal = fast_fwd is True and last_outcome in (
        RebaseOutcomeKind.REMOTE_PUSHED,
        RebaseOutcomeKind.COMPLETED,
        RebaseOutcomeKind.TERMINAL,
    )
    is_clean_ff = (
        fast_fwd is True
        and last_outcome == RebaseOutcomeKind.FAST_FORWARDED
        and not has_policy_pub
    )
    is_terminal = last_outcome in (RebaseOutcomeKind.COMPLETED, RebaseOutcomeKind.TERMINAL)
    return is_landed_terminal or is_clean_ff or is_terminal


def next_integration_action(
    record: object,
    policy: object = None,
    clock: object = None,
    *,
    proof: object = None,
) -> IntegrationAction:
    """Compute the next integration action from an integration record or rebase state.

    The single authority for integration progression decisions.
    Requires proof for completion and uses injected clock for durable deadline.
    """
    has_proof = proof is True or (proof is not None and proof is not False)
    if record is None:
        return (
            IntegrationAction.COMPLETE_WITH_PROOF
            if has_proof
            else IntegrationAction.REQUIRE_INTERVENTION
        )

    action = (
        _check_intervention(record)
        or _check_active_or_pending(record)
        or _check_rebase_state_action(record, policy, clock=clock)
    )
    if action is not None:
        return action

    if _has_completion_proof(record, policy=policy, proof=proof):
        return IntegrationAction.COMPLETE_WITH_PROOF

    return IntegrationAction.REQUIRE_INTERVENTION


__all__ = [
    "IntegrationAction",
    "RebaseOutcomeKind",
    "last_outcome_reason",
    "next_integration_action",
]
