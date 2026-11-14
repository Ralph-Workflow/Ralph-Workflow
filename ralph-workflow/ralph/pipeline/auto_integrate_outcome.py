from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.git.rebase.rebase import (
    RebaseConflicts,
    RebaseFailed,
    RebaseNoOp,
    RebaseSuccess,
)
from ralph.git.resolution_failure_cause import ResolutionFailureCause
from ralph.pipeline._integration_action import RebaseOutcomeKind
from ralph.pipeline.rebase_state import RebaseState
from ralph.pipeline.resolution_outcome import (
    ConfigFailureOutcome,
    NextStrategyOutcome,
    ResolvedOutcome,
    RetryLaterOutcome,
    UnknownOutcome,
    WaitingOutcome,
    assert_never,
)
from ralph.pipeline.resolution_outcome_producer import RETRY_LATER_REASON

if TYPE_CHECKING:
    from ralph.git.merge import MergeResult
    from ralph.pipeline.conflict_resolution._resolution_termination_reason import (
        ResolutionTerminationReason,
    )

ACTION_SKIPPED = RebaseOutcomeKind.SKIPPED.value
ACTION_REBASED = RebaseOutcomeKind.REBASED.value
ACTION_MERGED = RebaseOutcomeKind.MERGED.value
ACTION_CONFLICT = RebaseOutcomeKind.CONFLICT.value
ACTION_RECOVERED = RebaseOutcomeKind.RECOVERED.value


def record_skip(
    *,
    reason: str,
    target: str | None,
    fast_forwarded: bool = False,
    failure_cause: ResolutionFailureCause = ResolutionFailureCause.NONE,
) -> RebaseState:
    return RebaseState(
        last_outcome=RebaseOutcomeKind.SKIPPED,
        last_reason=reason,
        last_failure_cause=failure_cause,
        last_target=target,
        fast_forwarded=fast_forwarded,
    )


def record_conflict(
    *,
    reason: str,
    target: str | None,
    failure_cause: ResolutionFailureCause = ResolutionFailureCause.UNRESOLVED_CONFLICT,
) -> RebaseState:
    return RebaseState(
        last_outcome=RebaseOutcomeKind.CONFLICT,
        last_reason=reason,
        last_failure_cause=failure_cause,
        last_target=target,
        fast_forwarded=False,
    )


def record_resolution_exhausted(
    *,
    reason: str,
    target: str | None,
    failure_cause: ResolutionFailureCause = ResolutionFailureCause.UNRESOLVED_CONFLICT,
    terminal_reason: ResolutionTerminationReason | None = None,
) -> RebaseState:
    exhaustion_reason = reason
    if terminal_reason is not None:
        prefix = f"{terminal_reason.value}: "
        if not exhaustion_reason.startswith(prefix):
            exhaustion_reason = f"{prefix}{exhaustion_reason}"
    return RebaseState(
        last_outcome=RebaseOutcomeKind.CONFLICT,
        last_reason=reason,
        last_failure_cause=failure_cause,
        last_target=target,
        fast_forwarded=False,
        resolution_exhausted=True,
        resolution_exhaustion_reason=exhaustion_reason,
    )


def record_resolution_outcome(
    outcome: StopResolutionOutcome,
    *,
    target: str | None,
    retry_kind: RebaseOutcomeKind = RebaseOutcomeKind.CONFLICT,
) -> RebaseState:
    result: RebaseState
    match outcome:
        case ResolvedOutcome():
            result = RebaseState(
                last_outcome=RebaseOutcomeKind.CONFLICT_RESOLUTION,
                last_reason="conflict resolution completed",
                last_failure_cause=ResolutionFailureCause.NONE,
                last_target=target,
                fast_forwarded=False,
            )
        case WaitingOutcome(external_condition=cond):
            result = record_conflict(
                reason=cond,
                target=target,
                failure_cause=ResolutionFailureCause.CONTEXT_FAILURE,
            )
        case RetryLaterOutcome(until=until, on_change=on_change):
            message = on_change or (
                f"retry after {until}" if until is not None else RETRY_LATER_REASON
            )
            if retry_kind is RebaseOutcomeKind.SKIPPED:
                result = record_skip(
                    reason=message,
                    target=target,
                    failure_cause=ResolutionFailureCause.CONTEXT_FAILURE,
                )
            else:
                result = record_conflict(
                    reason=message,
                    target=target,
                    failure_cause=ResolutionFailureCause.AGENT_FAILURE,
                )
        case NextStrategyOutcome(strategy=strategy):
            result = record_conflict(
                reason=f"continuing with autonomous strategy: {strategy}",
                target=target,
                failure_cause=ResolutionFailureCause.UNRESOLVED_CONFLICT,
            )
        case ConfigFailureOutcome(file=file):
            result = record_conflict(
                reason=f"fix {file} then run ralph --check-config",
                target=target,
                failure_cause=ResolutionFailureCause.CONFIG_FAILURE,
            )
        case UnknownOutcome(error=err):
            result = record_conflict(
                reason=str(err),
                target=target,
                failure_cause=ResolutionFailureCause.CONTEXT_FAILURE,
            )
        case _ as unreachable:
            assert_never(unreachable)  # type: ignore[arg-type]
    return result


def record_rebase_outcome(
    *,
    rebase_outcome: RebaseSuccess | RebaseConflicts | RebaseNoOp | RebaseFailed,
    merge_attempted: bool,
    merge_outcome: MergeResult | None,
    target: str,
) -> RebaseState:
    action, reason = classify_rebase_outcome(
        rebase_outcome=rebase_outcome,
        merge_attempted=merge_attempted,
        merge_outcome=merge_outcome,
    )
    outcome = RebaseOutcomeKind.from_legacy(action)
    failure_cause = ResolutionFailureCause.NONE
    if outcome == RebaseOutcomeKind.CONFLICT:
        if merge_outcome is not None and merge_outcome.failure_cause != ResolutionFailureCause.NONE:
            failure_cause = merge_outcome.failure_cause
        else:
            failure_cause = ResolutionFailureCause.UNRESOLVED_CONFLICT
    return RebaseState(
        last_outcome=outcome,
        last_reason=reason,
        last_failure_cause=failure_cause,
        last_target=target,
        fast_forwarded=False,
    )


def classify_rebase_outcome(
    *,
    rebase_outcome: RebaseSuccess | RebaseConflicts | RebaseNoOp | RebaseFailed,
    merge_attempted: bool,
    merge_outcome: MergeResult | None,
) -> tuple[str, str]:
    if isinstance(rebase_outcome, RebaseConflicts):
        reason = (
            merge_outcome.reason
            if merge_attempted and merge_outcome is not None and merge_outcome.reason
            else "rebase conflicts"
        )
        return ACTION_CONFLICT, reason
    if isinstance(rebase_outcome, RebaseNoOp):
        return ACTION_SKIPPED, rebase_outcome.reason
    if isinstance(rebase_outcome, RebaseFailed):
        return ACTION_CONFLICT, f"rebase failed: {rebase_outcome.kind.kind.value}"
    if merge_attempted:
        if merge_outcome is None:
            return ACTION_CONFLICT, "branch is up to date"
        return ACTION_MERGED, "branch merged cleanly"
    return ACTION_REBASED, "branch rebased cleanly"


def classify_merge_only_outcome(
    merge_outcome: MergeResult | None,
) -> tuple[str, str | None]:
    if merge_outcome is None:
        return ACTION_CONFLICT, "endpoint merge attempt raised"
    if merge_outcome.outcome == RESOLUTION_FAILED:
        return ACTION_CONFLICT, "conflict resolution failed; merge aborted"
    if merge_outcome.outcome in {"success", "noop"}:
        return ACTION_MERGED, None
    if merge_outcome.reason:
        return ACTION_CONFLICT, f"endpoint {merge_outcome.reason}"
    return ACTION_CONFLICT, f"endpoint merge conflicted ({merge_outcome.outcome})"


__all__ = [
    "ACTION_CONFLICT",
    "ACTION_MERGED",
    "ACTION_REBASED",
    "ACTION_RECOVERED",
    "ACTION_SKIPPED",
    "classify_merge_only_outcome",
    "classify_rebase_outcome",
    "record_conflict",
    "record_rebase_outcome",
    "record_resolution_exhausted",
    "record_resolution_outcome",
    "record_skip",
]
