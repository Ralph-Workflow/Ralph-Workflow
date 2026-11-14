from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import assert_never

from loguru import logger

from ralph.git.merge import (
    MERGE_STATE_IN_PROGRESS,
    MERGE_STATE_NONE,
    MergeResult,
    abort_merge,
    merge_state,
    merge_target_into_current,
    paths_with_conflict_markers,
    stage_paths,
    staged_conflict_marker_paths,
    unmerged_paths,
)
from ralph.git.resolution_failure_cause import ResolutionFailureCause
from ralph.pipeline._pending_merge_commit import (
    _git_value,
    mark_pending_merge,
    prepare_pending_merge,
    resume_pending_merge,
)
from ralph.pipeline._retained_resolution_scope import retained_merge_paths
from ralph.pipeline.auto_integrate_record import IntegrationRecord, read_record
from ralph.pipeline.conflict_resolution._resolution_termination_reason import (
    ResolutionTerminationReason,
)
from ralph.pipeline.conflict_resolution.attempt_fault import (
    RESOLVER_NOT_SPENT_TERMINATION_REASONS,
)
from ralph.pipeline.conflict_resolution.resolution_outcome import ResolutionOutcome
from ralph.pipeline.resolution_outcome import (
    ConfigFailureOutcome,
    NextStrategyOutcome,
    ResolvedOutcome,
    RetryLaterOutcome,
    StopResolutionOutcome,
    UnknownOutcome,
    WaitingOutcome,
)
from ralph.pipeline.resolution_outcome_config_failure import ConfigFailure
from ralph.pipeline.resolution_outcome_exhausted import Exhausted
from ralph.pipeline.resolution_outcome_needs_operator import NeedsOperator
from ralph.pipeline.resolution_outcome_producer import classify_resolution_failure
from ralph.pipeline.resolution_outcome_resolved import Resolved
from ralph.pipeline.resolution_outcome_retry_later import RetryLater

_UNMERGED_QUERY_FAILED = "<unmerged-path-query-failed>"

ConflictResolver = Callable[[Path, str], StopResolutionOutcome | bool]


RESOLUTION_FAILED = "resolution_failed"
MERGE_COMMIT_PENDING = "merge_commit_pending"
RESOLUTION_AGENT_FAILURE = "resolution_agent_failure"
_RETAINED_RESOLUTION_REASON = "conflict resolution incomplete; progress retained"
_SPENT_RESOLVERS_PREFIX = "all resolver agents are spent/unavailable; "
_NOT_SPENT_TERMINAL_REASONS = RESOLVER_NOT_SPENT_TERMINATION_REASONS - {
    ResolutionTerminationReason.OUT_OF_REACH,
    ResolutionTerminationReason.CANDIDATE_EXHAUSTED,
    ResolutionTerminationReason.CONFIG_FAILURE,
    ResolutionTerminationReason.CONTEXT_FAILURE,
}



def resolution_gate_record(root: Path, target: str) -> IntegrationRecord:
    record = read_record(root)
    if record is not None:
        return record
    from ralph.git.rebase.rebase_continuation import (
        RebaseContinuationError,
        rebase_in_progress_at,
    )
    from ralph.pipeline.conflict_resolution.rebase_loop import current_rebase_identity

    try:
        in_rebase = rebase_in_progress_at(root)
    except (RebaseContinuationError, Exception):
        in_rebase = False

    if in_rebase:
        try:
            orig_head, onto = current_rebase_identity(root)
            return IntegrationRecord(
                phase="integrating",
                target=target,
                pre_feature_sha=orig_head,
                pre_target_sha=onto,
            )
        except Exception:
            pass
    return IntegrationRecord(
        phase="integrating",
        target=target,
        pre_feature_sha=_git_value(root, "rev-parse", "--verify", "HEAD") or "",
        pre_target_sha=_git_value(root, "rev-parse", "--verify", "MERGE_HEAD"),
    )


def endpoint_merge_with_resolution(
    root: Path,
    target: str,
    resolver: ConflictResolver | None,
) -> MergeResult | None:
    keep = resolver is not None
    try:
        result = merge_target_into_current(root, target, keep_conflicts=keep)
    except Exception as merge_exc:
        logger.warning("auto_integrate: endpoint merge raised: {}", merge_exc)
        return _retain_uncertain_merge(root, target, f"merge command failed: {merge_exc}")
    if result.outcome != "conflict" or resolver is None:
        return result
    state = merge_state(root)
    if state != MERGE_STATE_IN_PROGRESS:
        if state != MERGE_STATE_NONE:
            return _retain_uncertain_merge(
                root, target, "merge state unreadable; progress retained"
            )
        return result
    resolved = _resolve_and_commit_with_reason(root, target, resolver)
    if resolved.outcome not in {"success", MERGE_COMMIT_PENDING}:
        _abort_merge_safely(root)
    return resolved


def _retain_uncertain_merge(root: Path, target: str, reason: str) -> MergeResult:
    try:
        mark_pending_merge(root, target, resolving=True)
    except Exception as exc:
        reason = f"{reason}; ownership persistence failed: {exc}"
    return MergeResult(
        MERGE_COMMIT_PENDING, reason, failure_cause=ResolutionFailureCause.CONTEXT_FAILURE
    )


def _resolve_and_commit(
    root: Path,
    target: str,
    resolver: ConflictResolver,
) -> bool:
    return _resolve_and_commit_with_reason(root, target, resolver).outcome == "success"


def resolve_and_commit_with_reason(
    root: Path,
    target: str,
    resolver: ConflictResolver,
) -> MergeResult:
    conflicted = unmerged_paths(root) or staged_conflict_marker_paths(root)
    retained = read_record(root)
    if retained is not None and retained.resolving_merge:
        conflicted = list(retained_merge_paths(root, tuple(conflicted)))
    elif not conflicted:
        return _stage_verify_and_commit(root, [], target)
    refusal = _merge_scope_refusal(root, target, conflicted)
    if refusal is not None:
        return refusal
    try:
        from ralph.pipeline._stop_resolution_entry import attempt_stop_resolution
        from ralph.pipeline.stop_resolution_blocked import StopResolutionBlocked
        from ralph.pipeline.stop_resolution_trigger import StopResolutionTrigger

        result = attempt_stop_resolution(
            None,
            StopResolutionTrigger(
                name="merge_stop",
                root=root,
                target=target,
                resolver=resolver,
                record=resolution_gate_record(root, target),
                launcher=lambda: _merge_resolution_result(
                    root,
                    target,
                    resolver,
                    tuple(conflicted),
                ),
            ),
        )
        if isinstance(result, StopResolutionBlocked):
            result = MergeResult(
                MERGE_COMMIT_PENDING,
                result.reason,
                failure_cause=ResolutionFailureCause.UNRESOLVED_CONFLICT,
            )
        if not isinstance(result, MergeResult):
            raise TypeError("merge stop resolution returned an invalid result")
    except Exception as resolver_exc:
        logger.warning("auto_integrate: conflict resolver raised: {}", resolver_exc)
        return _resolver_exception_result(resolver_exc, tuple(conflicted))
    if result.outcome != "success":
        return result
    return _commit_resolved_merge(root, conflicted, target)


def _merge_scope_refusal(
    root: Path,
    target: str,
    conflicted: list[str],
) -> MergeResult | None:
    if _UNMERGED_QUERY_FAILED in conflicted or "<staged-marker-query-failed>" in conflicted:
        logger.warning(
            "auto_integrate: no readable conflicted paths to resolve: {}",
            conflicted,
        )
        return MergeResult(
            MERGE_COMMIT_PENDING,
            "no readable conflicted paths; resolution retained",
            failure_cause=ResolutionFailureCause.CONTEXT_FAILURE,
        )
    try:
        mark_pending_merge(root, target, resolving=True)
    except Exception as exc:
        return MergeResult(
            MERGE_COMMIT_PENDING,
            f"cannot protect merge resolution: {exc}",
            failure_cause=ResolutionFailureCause.CONTEXT_FAILURE,
        )
    return None


def _commit_resolved_merge(root: Path, conflicted: list[str], target: str) -> MergeResult:
    if not conflicted:
        return MergeResult(
            MERGE_COMMIT_PENDING,
            "original resolution scope unavailable; progress retained",
            failure_cause=ResolutionFailureCause.CONTEXT_FAILURE,
        )
    return _stage_verify_and_commit(root, conflicted, target)


def _merge_resolution_result(
    root: Path,
    target: str,
    resolver: ConflictResolver,
    conflicted: tuple[str, ...],
) -> MergeResult:
    outcome = resolver(root, target)
    return handle_unsuccessful_resolution(outcome, None, conflicted=conflicted)


def _resolver_exception_result(
    resolver_exc: BaseException,
    conflicted: tuple[str, ...],
) -> MergeResult:
    reason = f"resolver raised: {resolver_exc}; partial resolution retained"
    outcome = classify_resolution_failure(
        terminal_reason=None,
        failure_cause=ResolutionFailureCause.AGENT_FAILURE,
        reason=reason,
        paths=conflicted,
        config_error=resolver_exc,
    )
    match outcome:
        case ConfigFailure(file=file):
            return MergeResult(
                outcome=MERGE_COMMIT_PENDING,
                reason=f"fix {file} then run ralph --check-config",
                failure_cause=ResolutionFailureCause.CONFIG_FAILURE,
            )
        case Resolved() | NeedsOperator() | RetryLater() | Exhausted():
            return MergeResult(
                outcome=MERGE_COMMIT_PENDING,
                reason=reason,
                failure_cause=ResolutionFailureCause.AGENT_FAILURE,
            )
        case _:
            raise AssertionError(f"unexpected outcome: {outcome!r}")


def _terminal_reason(
    result: ResolutionOutcome | bool,
    message: str | None,
) -> ResolutionTerminationReason | None:
    if isinstance(result, ResolutionOutcome) and isinstance(
        result.reason, ResolutionTerminationReason
    ):
        return result.reason
    head = (message or "").partition(":")[0].strip()
    if not head:
        return None
    try:
        return ResolutionTerminationReason(head)
    except ValueError:
        return None


def _merge_resolution_facts(
    result: ResolutionOutcome | bool,
    reason: str | None,
) -> tuple[ResolutionTerminationReason | None, ResolutionFailureCause, str | None]:
    message = reason
    failure_cause = ResolutionFailureCause.UNRESOLVED_CONFLICT
    if isinstance(result, ResolutionOutcome):
        if result.failure_cause is not ResolutionFailureCause.NONE:
            failure_cause = result.failure_cause
        if message is None:
            message = result.exhaustion_message or (
                str(result.reason) if result.reason is not None else None
            )
    terminal_reason = _terminal_reason(result, message)
    if terminal_reason is not None and terminal_reason in _NOT_SPENT_TERMINAL_REASONS:
        failure_cause = ResolutionFailureCause.AGENT_FAILURE
        message = RESOLUTION_AGENT_FAILURE
    return terminal_reason, failure_cause, message


def _merge_conflicted_paths(
    result: ResolutionOutcome | bool,
    conflicted: tuple[str, ...],
) -> tuple[str, ...]:
    if isinstance(result, ResolutionOutcome) and result.unresolved_paths:
        return tuple(result.unresolved_paths)
    return conflicted


def _handle_sealed_outcome(
    result: StopResolutionOutcome,
    reason: str | None,
) -> MergeResult:
    match result:
        case ResolvedOutcome():
            return MergeResult("success", failure_cause=ResolutionFailureCause.NONE)
        case WaitingOutcome(external_condition=condition, resumes_when=resumes_when):
            return MergeResult(
                outcome=MERGE_COMMIT_PENDING,
                reason=f"waiting for {condition}; resumes when {resumes_when}",
                failure_cause=ResolutionFailureCause.CONTEXT_FAILURE,
            )
        case RetryLaterOutcome(until=until, on_change=on_change):
            return MergeResult(
                outcome=MERGE_COMMIT_PENDING,
                reason=reason
                or on_change
                or (f"retry after {until}" if until is not None else _RETAINED_RESOLUTION_REASON),
                failure_cause=ResolutionFailureCause.AGENT_FAILURE,
            )
        case NextStrategyOutcome(strategy=strategy):
            return MergeResult(
                outcome=MERGE_COMMIT_PENDING,
                reason=f"continuing with autonomous strategy: {strategy}",
                failure_cause=ResolutionFailureCause.UNRESOLVED_CONFLICT,
            )
        case ConfigFailureOutcome(file=file):
            return MergeResult(
                outcome=MERGE_COMMIT_PENDING,
                reason=f"fix {file} then run ralph --check-config",
                failure_cause=ResolutionFailureCause.CONFIG_FAILURE,
            )
        case UnknownOutcome(error=error):
            return MergeResult(
                outcome=MERGE_COMMIT_PENDING,
                reason=f"unclassified resolution failure; advancing strategy: {error}",
                failure_cause=ResolutionFailureCause.CONTEXT_FAILURE,
            )
        case _:
            raise AssertionError(f"unexpected outcome: {outcome!r}")


def handle_unsuccessful_resolution(
    result: StopResolutionOutcome | ResolutionOutcome | bool,
    reason: str | None,
    conflicted: tuple[str, ...] = (),
) -> MergeResult:
    if isinstance(
        result,
        (
            ResolvedOutcome,
            WaitingOutcome,
            RetryLaterOutcome,
            NextStrategyOutcome,
            ConfigFailureOutcome,
            UnknownOutcome,
        ),
    ):
        return _handle_sealed_outcome(result, reason)

    terminal_reason, failure_cause, message = _merge_resolution_facts(result, reason)
    paths = _merge_conflicted_paths(result, conflicted)
    outcome = classify_resolution_failure(
        terminal_reason=terminal_reason,
        failure_cause=failure_cause,
        reason=message or _RETAINED_RESOLUTION_REASON,
        paths=paths,
    )
    match outcome:
        case Resolved():
            return MergeResult("success", failure_cause=ResolutionFailureCause.NONE)
        case RetryLater(until=until, on_change=on_change):
            return MergeResult(
                outcome=MERGE_COMMIT_PENDING,
                reason=reason or on_change or f"retry after {until}",
                failure_cause=failure_cause,
            )
        case NeedsOperator(cause=cause, paths=paths, command=command):
            return MergeResult(
                outcome=MERGE_COMMIT_PENDING,
                reason=f"conflict on {', '.join(paths)} needs operator action: {command}",
                failure_cause=cause,
            )
        case Exhausted(agents=agents):
            agents_desc = f": {', '.join(agents)}" if agents else ""
            if paths:
                from ralph.pipeline.conflict_resolution._operator_command import (
                    operator_command_for_paths,
                )

                reason_msg = (
                    f"all resolver agents are spent/unavailable{agents_desc}; conflict on "
                    f"{', '.join(paths)} needs operator action: {operator_command_for_paths(paths)}"
                )
            else:
                reason_msg = f"all resolver agents are spent/unavailable{agents_desc}"
            return MergeResult(
                outcome=MERGE_COMMIT_PENDING,
                reason=reason_msg,
                failure_cause=ResolutionFailureCause.UNRESOLVED_CONFLICT,
            )
        case ConfigFailure(file=file):
            return MergeResult(
                outcome=MERGE_COMMIT_PENDING,
                reason=f"fix {file} then run ralph --check-config",
                failure_cause=ResolutionFailureCause.CONFIG_FAILURE,
            )
        case _:
            raise AssertionError(f"unexpected outcome: {outcome!r}")


_handle_unsuccessful_resolution = handle_unsuccessful_resolution
_resolve_and_commit_with_reason = resolve_and_commit_with_reason


def _clear_ort_residue(root: Path, conflicted: tuple[str, ...]) -> None:
    from ralph.pipeline.conflict_resolution.rebase_loop import _remove_ort_residue

    try:
        _remove_ort_residue(root, conflicted)
    except Exception as exc:
        logger.warning("auto_integrate: could not clear ort residue: {}", exc)


def _stage_verify_and_commit(root: Path, conflicted: list[str], target: str) -> MergeResult:
    try:
        return _stage_and_verify(root, conflicted, target)
    except Exception as exc:
        return MergeResult(
            MERGE_COMMIT_PENDING,
            f"resolution staging failed: {exc}; progress retained",
            failure_cause=ResolutionFailureCause.CONTEXT_FAILURE,
        )


def _stage_and_verify(root: Path, conflicted: list[str], target: str) -> MergeResult:
    _clear_ort_residue(root, tuple(conflicted))
    if not stage_paths(root, conflicted):
        logger.warning("auto_integrate: failed to stage resolved paths: {}", conflicted)
        return MergeResult(
            MERGE_COMMIT_PENDING,
            "the resolution did not prove out against the worktree; progress retained",
            failure_cause=ResolutionFailureCause.UNRESOLVED_CONFLICT,
        )
    marked = [
        *paths_with_conflict_markers(root, conflicted),
        *staged_conflict_marker_paths(root),
    ]
    if marked:
        logger.warning(
            "auto_integrate: conflict markers remain after resolution: {}",
            marked,
        )
        return MergeResult(
            MERGE_COMMIT_PENDING,
            "the resolution did not prove out against the worktree; progress retained",
            failure_cause=ResolutionFailureCause.UNRESOLVED_CONFLICT,
        )
    remaining = unmerged_paths(root)
    if remaining:
        logger.warning("auto_integrate: conflicts remain after resolution: {}", remaining)
        return MergeResult(
            MERGE_COMMIT_PENDING,
            "the resolution did not prove out against the worktree; progress retained",
            failure_cause=ResolutionFailureCause.UNRESOLVED_CONFLICT,
        )
    return _commit_verified_merge(root, target)


def _commit_verified_merge(root: Path, target: str) -> MergeResult:
    try:
        prepared_error = prepare_pending_merge(root, target)
        if prepared_error is not None:
            return MergeResult(
                MERGE_COMMIT_PENDING,
                prepared_error,
                failure_cause=ResolutionFailureCause.CONTEXT_FAILURE,
            )
        record = read_record(root)
        if record is None:
            return MergeResult(
                MERGE_COMMIT_PENDING,
                "prepared merge record unreadable",
                failure_cause=ResolutionFailureCause.CONTEXT_FAILURE,
            )
        resumed = resume_pending_merge(root, record)
        if isinstance(resumed, str):
            return MergeResult(
                MERGE_COMMIT_PENDING,
                resumed,
                failure_cause=ResolutionFailureCause.CONTEXT_FAILURE,
            )
    except Exception as exc:
        return MergeResult(
            MERGE_COMMIT_PENDING,
            f"verified merge commit pending: {exc}",
            failure_cause=ResolutionFailureCause.CONTEXT_FAILURE,
        )
    return MergeResult("success", failure_cause=ResolutionFailureCause.NONE)


def _abort_merge_safely(root: Path) -> None:
    try:
        if abort_merge(root):
            return
        state = merge_state(root)
        if state != MERGE_STATE_NONE:
            logger.warning(
                "auto_integrate: merge not proven aborted in {} (state {}); "
                "later integrations will be blocked until it is resolved",
                root,
                state,
            )
    except Exception as abort_exc:
        logger.warning("auto_integrate: abort_merge failed: {}", abort_exc)
