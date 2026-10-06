"""Agent-assisted conflict resolution for the auto-integrate endpoint merge.

When the endpoint merge of the mainline into the feature branch
conflicts, the pipeline can hand the conflicted working tree to a
conflict resolver — in production a focused dev-agent invocation whose
ONLY job is to rewrite the conflicted files in place. The resolver
never runs a git command: Ralph stages the previously-conflicted paths
itself and verifies the result, because an agent running under Ralph's
own MCP exec policy is denied every git invocation. The merge commit
itself is always created deterministically by
:func:`ralph.git.merge.commit_merge_in_progress`, never by the agent,
so the integration flow (fast-forward of the mainline, crash records,
state receipts) stays byte-deterministic around the agent call.

Fault-tolerance contract:

* A resolver failure or interruption retains its partial edits and merge
  metadata so the next agent continues the same resolution.
* A merge that conflicts without leaving ``MERGE_HEAD`` (refused
  pre-start) is returned as a plain conflict — there is nothing for a
  resolver to repair. That verdict is read through
  :func:`ralph.git.merge.merge_state`, never its boolean projection:
  when the git query itself fails the merge state is UNKNOWN, which is
  not evidence of a clean tree, so the abort is attempted rather than
  assumed unnecessary.
* A resolution that leaves a conflict marker in any previously
  conflicted file is REFUSED. ``git add`` on a marker-bearing file
  silently clears its unmerged state, so the git-authoritative
  ``unmerged_paths`` check alone cannot prove a real resolution.
* A verified resolution whose commit fails retains its index, merge state,
  and durable ownership record for a commit-only retry.
"""

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
from ralph.pipeline._pending_merge_commit import (
    mark_pending_merge,
    prepare_pending_merge,
    resume_pending_merge,
)
from ralph.pipeline._retained_resolution_scope import retained_merge_paths
from ralph.pipeline.auto_integrate_record import read_record
from ralph.pipeline.conflict_resolution.attempt_fault import (
    RESOLVER_NOT_SPENT_TERMINATION_REASONS,
)
from ralph.pipeline.conflict_resolution.resolution_outcome import ResolutionOutcome

#: Sentinel :func:`unmerged_paths` reports when the git query itself
#: failed, so a broken repository is never mistaken for "resolved".
_UNMERGED_QUERY_FAILED = "<unmerged-path-query-failed>"

#: Signature of a conflict resolver: ``(repo_root, target_branch) ->``
#: :class:`ResolutionOutcome`. The typed result preserves terminal failure
#: evidence for the integration producer instead of collapsing it to a bool.
ConflictResolver = Callable[[Path, str], ResolutionOutcome | bool]


def _resolution_succeeded(result: ResolutionOutcome | bool) -> bool:
    """Project legacy injected boolean fakes onto the typed resolver contract."""
    return result.succeeded if isinstance(result, ResolutionOutcome) else result


#: MergeResult outcome recorded when a resolver was given the conflict
#: and could not (or did not) fully resolve it. Distinct from plain
#: ``'conflict'`` so the operator-facing reason names the failed
#: resolution attempt.
RESOLUTION_FAILED = "resolution_failed"
MERGE_COMMIT_PENDING = "merge_commit_pending"

#: A resolver-agent failure is handled by conflict-resolution recovery
#: (cooldown/fallover), not charged as a repository conflict attempt.
RESOLUTION_AGENT_FAILURE = "resolution_agent_failure"


def endpoint_merge_with_resolution(
    root: Path,
    target: str,
    resolver: ConflictResolver | None,
) -> MergeResult | None:
    """Attempt the endpoint merge; on conflict, optionally resolve it.

    Returns the final :class:`MergeResult` or ``None`` when the merge
    attempt itself raised (the caller records the exception headline).
    With a resolver, a conflicted merge is left in progress, the
    resolver runs, and on full resolution (no unmerged paths) the
    merge is committed deterministically; unfinished resolution retains
    progress for supervised continuation.
    """
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
        # The merge refused to start (no MERGE_HEAD): there are no
        # conflict markers on disk for a resolver to repair.
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
    return MergeResult(MERGE_COMMIT_PENDING, reason)


def _resolve_and_commit(
    root: Path,
    target: str,
    resolver: ConflictResolver,
) -> bool:
    """Backwards-compatible boolean projection of the typed result."""
    return _resolve_and_commit_with_reason(root, target, resolver).outcome == "success"


def _resolve_and_commit_with_reason(
    root: Path,
    target: str,
    resolver: ConflictResolver,
) -> MergeResult:
    """Run the resolver against the in-progress merge and commit it.

    True only when the resolver reported success, Ralph staged every
    previously-conflicted path, no conflict marker survived, no
    unmerged path remains, and the deterministic merge commit landed.
    Resolver exceptions retain the durable resolution ownership and edits
    so the next supervised invocation can continue the same work.
    """
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
            MERGE_COMMIT_PENDING, "no readable conflicted paths; resolution retained"
        )
    try:
        mark_pending_merge(root, target, resolving=True)
    except Exception as exc:
        return MergeResult(MERGE_COMMIT_PENDING, f"cannot protect merge resolution: {exc}")
    try:
        result = resolver(root, target)
    except Exception as resolver_exc:
        logger.warning("auto_integrate: conflict resolver raised: {}", resolver_exc)
        return MergeResult(
            MERGE_COMMIT_PENDING, f"resolver raised: {resolver_exc}; partial resolution retained"
        )
    reason = (
        result.reason.value
        if isinstance(result, ResolutionOutcome) and result.reason is not None
        else None
    )
    if not _resolution_succeeded(result):
        if (
            isinstance(result, ResolutionOutcome)
            and result.reason in RESOLVER_NOT_SPENT_TERMINATION_REASONS
        ):
            reason = RESOLUTION_AGENT_FAILURE
        return MergeResult(
            MERGE_COMMIT_PENDING, reason or "conflict resolution incomplete; progress retained"
        )
    return (
        _stage_verify_and_commit(root, conflicted, target) if conflicted
        else MergeResult(MERGE_COMMIT_PENDING, "original resolution scope unavailable; progress retained")
    )


def _clear_ort_residue(root: Path, conflicted: tuple[str, ...]) -> None:
    """Drop the ``path~LABEL`` files git's ort backend leaves behind.

    A file-vs-directory conflict makes ort park a side under an invented
    name. The rebase loop has always removed them; the merge seam did
    not, so `x~HEAD` -- a path no one wrote and no side has -- was
    staged and committed as a real file. Never raises.
    """
    from ralph.pipeline.conflict_resolution.rebase_loop import _remove_ort_residue

    try:
        _remove_ort_residue(root, conflicted)
    except Exception as exc:  # pragma: no cover -- defensive
        logger.warning("auto_integrate: could not clear ort residue: {}", exc)


def _stage_verify_and_commit(root: Path, conflicted: list[str], target: str) -> MergeResult:
    try:
        return _stage_and_verify(root, conflicted, target)
    except Exception as exc:
        return MergeResult(
            MERGE_COMMIT_PENDING, f"resolution staging failed: {exc}; progress retained"
        )


def _stage_and_verify(root: Path, conflicted: list[str], target: str) -> MergeResult:
    """Stage the conflicted paths, prove the resolution, commit the merge.

    Staging is scoped to exactly the paths that were unmerged BEFORE
    the resolver ran — never ``git add -A`` — so an unrelated file the
    agent touched is not swept into the merge commit. The marker scan
    runs AFTER staging on purpose: ``git add`` clears the unmerged bit,
    so the textual scan is the only remaining proof of a real
    resolution. The git-authoritative unmerged check is retained as a
    second gate before the deterministic commit.
    """
    _clear_ort_residue(root, tuple(conflicted))
    if not stage_paths(root, conflicted):
        logger.warning("auto_integrate: failed to stage resolved paths: {}", conflicted)
        return MergeResult(
            MERGE_COMMIT_PENDING,
            "the resolution did not prove out against the worktree; progress retained",
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
        )
    remaining = unmerged_paths(root)
    if remaining:
        logger.warning("auto_integrate: conflicts remain after resolution: {}", remaining)
        return MergeResult(
            MERGE_COMMIT_PENDING,
            "the resolution did not prove out against the worktree; progress retained",
        )
    return _commit_verified_merge(root, target)


def _commit_verified_merge(root: Path, target: str) -> MergeResult:
    """Commit only after durable preparation; retain the merge on any commit failure."""
    try:
        prepared_error = prepare_pending_merge(root, target)
        if prepared_error is not None:
            return MergeResult(MERGE_COMMIT_PENDING, prepared_error)
        record = read_record(root)
        if record is None:
            return MergeResult(MERGE_COMMIT_PENDING, "prepared merge record unreadable")
        resumed = resume_pending_merge(root, record)
        if isinstance(resumed, str):
            return MergeResult(MERGE_COMMIT_PENDING, resumed)
    except Exception as exc:
        return MergeResult(MERGE_COMMIT_PENDING, f"verified merge commit pending: {exc}")
    return MergeResult("success")


def _abort_merge_safely(root: Path) -> None:
    """Abort any in-progress merge; never raises.

    A refused abort is reported rather than assumed successful: a
    stranded ``MERGE_HEAD`` blocks every subsequent integration, so the
    operator needs to see it the moment it happens. ``abort_merge``
    also returns False for the benign "there was nothing to abort"
    case, which is why the warning is gated on the post-abort state
    NOT being a positive :data:`MERGE_STATE_NONE`. An unreadable state
    warns too: it does not prove the merge is gone.
    """
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
