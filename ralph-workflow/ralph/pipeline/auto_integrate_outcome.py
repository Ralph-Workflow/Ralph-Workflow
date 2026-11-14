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
from ralph.pipeline.auto_integrate_resolve import RESOLUTION_FAILED
from ralph.pipeline.rebase_state import RebaseState
from ralph.pipeline.resolution_outcome import assert_never
from ralph.pipeline.resolution_outcome_config_failure import ConfigFailure
from ralph.pipeline.resolution_outcome_exhausted import Exhausted
from ralph.pipeline.resolution_outcome_needs_operator import NeedsOperator
from ralph.pipeline.resolution_outcome_producer import RETRY_LATER_REASON
from ralph.pipeline.resolution_outcome_resolved import Resolved
from ralph.pipeline.resolution_outcome_retry_later import RetryLater
from ralph.pipeline.resolution_outcome_type import ResolutionOutcomeType

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
    outcome: ResolutionOutcomeType,
    *,
    target: str | None,
    retry_kind: RebaseOutcomeKind = RebaseOutcomeKind.CONFLICT,
) -> RebaseState:
    result: RebaseState
    match outcome:
        case Resolved():
            result = RebaseState(
                last_outcome=RebaseOutcomeKind.CONFLICT_RESOLUTION,
                last_reason="conflict resolution completed",
                last_failure_cause=ResolutionFailureCause.NONE,
                last_target=target,
                fast_forwarded=False,
            )
        case NeedsOperator(cause=cause, paths=paths, command=command):
            result = record_conflict(
                reason=f"conflict on {', '.join(paths)} needs operator action: {command}",
                target=target,
                failure_cause=cause,
            )
        case RetryLater(until=until, on_change=on_change):
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
        case Exhausted(agents=agents):
            result = record_resolution_exhausted(
                reason=f"resolver agents exhausted: {', '.join(agents)}",
                target=target,
                terminal_reason=ResolutionTerminationReason.CANDIDATE_EXHAUSTED,
            )
        case ConfigFailure(file=file):
            result = record_conflict(
                reason=f"fix {file} then run ralph --check-config",
                target=target,
                failure_cause=ResolutionFailureCause.CONFIG_FAILURE,
            )
        case _ as unreachable:
            assert_never(unreachable)
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


def _classify_rebase_conflict_outcome(
    *,
    merge_attempted: bool,
    merge_outcome: MergeResult | None,
) -> tuple[str, str | None] | None:
    if not merge_attempted or merge_outcome is None:
        return None
    if merge_outcome.outcome == RESOLUTION_FAILED:
        reason = merge_outcome.reason
        return ACTION_CONFLICT, (
            f"conflict resolution did not complete ({reason}); merge aborted"
            if reason
            else "conflict resolution failed; merge aborted"
        )
    if merge_outcome.outcome == "conflict" and merge_outcome.reason:
        return ACTION_CONFLICT, f"endpoint {merge_outcome.reason}"
    if merge_outcome.outcome == "conflict":
        return ACTION_CONFLICT, "rebase and endpoint merge both conflicted"
    if merge_outcome.outcome in {"success", "noop"}:
        return ACTION_MERGED, None
    return None


def classify_rebase_outcome(
    *,
    rebase_outcome: RebaseSuccess | RebaseConflicts | RebaseNoOp | RebaseFailed,
    merge_attempted: bool,
    merge_outcome: MergeResult | None,
) -> tuple[str, str | None]:
    if isinstance(rebase_outcome, RebaseConflicts):
        sub = _classify_rebase_conflict_outcome(
            merge_attempted=merge_attempted,
            merge_outcome=merge_outcome,
        )
        if sub is not None:
            return sub
        return ACTION_CONFLICT, "rebase conflicts"
    if isinstance(rebase_outcome, RebaseNoOp):
        return _classify_rebase_noop_outcome(
            rebase_outcome=rebase_outcome,
            merge_attempted=merge_attempted,
            merge_outcome=merge_outcome,
        )
    if isinstance(rebase_outcome, RebaseFailed):
        return _classify_rebase_failed_outcome(
            rebase_outcome=rebase_outcome,
            merge_attempted=merge_attempted,
            merge_outcome=merge_outcome,
        )
    if merge_attempted and merge_outcome is not None:
        return ACTION_MERGED, None
    return ACTION_REBASED, None


def _classify_rebase_noop_outcome(
    *,
    rebase_outcome: RebaseNoOp,
    merge_attempted: bool,
    merge_outcome: MergeResult | None,
) -> tuple[str, str | None]:
    if merge_attempted:
        return classify_merge_only_outcome(merge_outcome)
    return ACTION_REBASED, rebase_outcome.reason


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


def _classify_rebase_failed_outcome(
    *,
    rebase_outcome: RebaseFailed,
    merge_attempted: bool,
    merge_outcome: MergeResult | None,
) -> tuple[str, str | None]:
    sub = _classify_rebase_conflict_outcome(
        merge_attempted=merge_attempted,
        merge_outcome=merge_outcome,
    )
    if sub is None:
        return ACTION_SKIPPED, f"rebase failed: {rebase_outcome.kind}"
    if sub == (ACTION_CONFLICT, "rebase and endpoint merge both conflicted"):
        return ACTION_CONFLICT, "rebase failed and endpoint merge conflicted"
    return sub


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
