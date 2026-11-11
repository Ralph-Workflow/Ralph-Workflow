
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

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
    mark_pending_merge,
    prepare_pending_merge,
    resume_pending_merge,
)
from ralph.pipeline._retained_resolution_scope import retained_merge_paths
from ralph.pipeline.auto_integrate_record import read_record
from ralph.pipeline.conflict_resolution._resolution_termination_reason import (
    ResolutionTerminationReason,
)
from ralph.pipeline.conflict_resolution.attempt_fault import (
    RESOLVER_NOT_SPENT_TERMINATION_REASONS,
)
from ralph.pipeline.conflict_resolution.resolution_outcome import ResolutionOutcome

_UNMERGED_QUERY_FAILED = "<unmerged-path-query-failed>"

ConflictResolver = Callable[[Path, str], ResolutionOutcome | bool]


def _resolution_succeeded(result: ResolutionOutcome | bool) -> bool:
    return result.succeeded if isinstance(result, ResolutionOutcome) else result


RESOLUTION_FAILED = "resolution_failed"
MERGE_COMMIT_PENDING = "merge_commit_pending"

RESOLUTION_AGENT_FAILURE = "resolution_agent_failure"


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
    try:
        result = resolver(root, target)
    except Exception as resolver_exc:
        logger.warning("auto_integrate: conflict resolver raised: {}", resolver_exc)
        return MergeResult(
            MERGE_COMMIT_PENDING,
            f"resolver raised: {resolver_exc}; partial resolution retained",
            failure_cause=ResolutionFailureCause.AGENT_FAILURE,
        )
    reason: str | None = None
    if isinstance(result, ResolutionOutcome):
        reason = result.exhaustion_message or (
            str(result.reason) if result.reason is not None else None
        )
    if not _resolution_succeeded(result):
        return _handle_unsuccessful_resolution(result, reason, conflicted=tuple(conflicted))
    return (
        _stage_verify_and_commit(root, conflicted, target)
        if conflicted
        else MergeResult(
            MERGE_COMMIT_PENDING,
            "original resolution scope unavailable; progress retained",
            failure_cause=ResolutionFailureCause.CONTEXT_FAILURE,
        )
    )


def handle_unsuccessful_resolution(
    result: ResolutionOutcome | bool,
    reason: str | None,
    conflicted: tuple[str, ...] = (),
) -> MergeResult:
    if (
        isinstance(result, ResolutionOutcome)
        and result.reason == ResolutionTerminationReason.OUT_OF_REACH
    ) or (isinstance(reason, str) and ResolutionTerminationReason.OUT_OF_REACH.value in reason):
        from ralph.pipeline.conflict_resolution.sight import gitlink_escalation_message

        paths = (
            tuple(result.unresolved_paths)
            if (isinstance(result, ResolutionOutcome) and result.unresolved_paths)
            else conflicted
        )
        out_of_reach_msg: str | None = (
            result.exhaustion_message if isinstance(result, ResolutionOutcome) else None
        )
        if not out_of_reach_msg or "needs operator action:" not in out_of_reach_msg:
            if reason and "needs operator action:" in reason:
                out_of_reach_msg = reason
            else:
                out_of_reach_msg = gitlink_escalation_message(paths)
        return MergeResult(
            outcome=MERGE_COMMIT_PENDING,
            reason=out_of_reach_msg,
            failure_cause=ResolutionFailureCause.UNRESOLVED_CONFLICT,
        )
    if (
        isinstance(result, ResolutionOutcome)
        and result.reason in RESOLVER_NOT_SPENT_TERMINATION_REASONS
    ):
        return MergeResult(
            MERGE_COMMIT_PENDING,
            RESOLUTION_AGENT_FAILURE,
            failure_cause=ResolutionFailureCause.AGENT_FAILURE,
        )
    if (
        isinstance(result, ResolutionOutcome)
        and result.reason == ResolutionTerminationReason.CANDIDATE_EXHAUSTED
    ) or (
        isinstance(reason, str) and ResolutionTerminationReason.CANDIDATE_EXHAUSTED.value in reason
    ):
        from ralph.pipeline.conflict_resolution._operator_command import operator_command_for_paths

        paths = (
            tuple(result.unresolved_paths)
            if (isinstance(result, ResolutionOutcome) and result.unresolved_paths)
            else conflicted
        )
        cmd = operator_command_for_paths(paths)
        paths_str = ", ".join(paths)
        exhaustion_msg: str | None = (
            result.exhaustion_message if isinstance(result, ResolutionOutcome) else None
        )
        if not exhaustion_msg or "needs operator action:" not in exhaustion_msg:
            if reason and "needs operator action:" in reason:
                exhaustion_msg = reason
            else:
                exhaustion_msg = (
                    f"all resolver agents are spent/unavailable; "
                    f"conflict on {paths_str} needs operator action: {cmd}"
                )
        return MergeResult(
            outcome=MERGE_COMMIT_PENDING,
            reason=exhaustion_msg,
            failure_cause=ResolutionFailureCause.UNRESOLVED_CONFLICT,
        )
    return MergeResult(
        outcome=MERGE_COMMIT_PENDING,
        reason=reason or "conflict resolution incomplete; progress retained",
        failure_cause=ResolutionFailureCause.UNRESOLVED_CONFLICT,
    )


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
