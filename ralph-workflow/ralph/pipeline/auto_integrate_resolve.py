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

* The resolver callable may fail or raise; both are contained here and
  reported as ``MergeResult(outcome='resolution_failed')`` after the
  in-progress merge is aborted, leaving the feature branch
  bit-identical to its pre-merge state.
* A merge that conflicts without leaving ``MERGE_HEAD`` (refused
  pre-start) is returned as a plain conflict — there is nothing for a
  resolver to repair.
* A resolution that leaves a conflict marker in any previously
  conflicted file is REFUSED. ``git add`` on a marker-bearing file
  silently clears its unmerged state, so the git-authoritative
  ``unmerged_paths`` check alone cannot prove a real resolution.
* This module never raises.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from loguru import logger

from ralph.git.merge import (
    MergeResult,
    abort_merge,
    commit_merge_in_progress,
    merge_in_progress,
    merge_target_into_current,
    paths_with_conflict_markers,
    stage_paths,
    unmerged_paths,
)

#: Sentinel :func:`unmerged_paths` reports when the git query itself
#: failed, so a broken repository is never mistaken for "resolved".
_UNMERGED_QUERY_FAILED = "<unmerged-path-query-failed>"

#: Signature of a conflict resolver: ``(repo_root, target_branch) ->
#: resolved``. Returning True means every conflict marker was rewritten
#: on disk; Ralph then stages, verifies and commits the merge.
ConflictResolver = Callable[[Path, str], bool]

#: MergeResult outcome recorded when a resolver was given the conflict
#: and could not (or did not) fully resolve it. Distinct from plain
#: ``'conflict'`` so the operator-facing reason names the failed
#: resolution attempt.
RESOLUTION_FAILED = "resolution_failed"


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
    merge is committed deterministically; any resolution failure
    aborts the merge and reports :data:`RESOLUTION_FAILED`.
    """
    keep = resolver is not None
    try:
        result = merge_target_into_current(root, target, keep_conflicts=keep)
    except Exception as merge_exc:
        logger.warning("auto_integrate: endpoint merge raised: {}", merge_exc)
        _abort_merge_safely(root)
        return None
    if result.outcome != "conflict" or resolver is None:
        return result
    if not merge_in_progress(root):
        # The merge refused to start (no MERGE_HEAD): there are no
        # conflict markers on disk for a resolver to repair.
        return result
    if _resolve_and_commit(root, target, resolver):
        return MergeResult(outcome="success")
    _abort_merge_safely(root)
    return MergeResult(outcome=RESOLUTION_FAILED)


def _resolve_and_commit(
    root: Path,
    target: str,
    resolver: ConflictResolver,
) -> bool:
    """Run the resolver against the in-progress merge and commit it.

    True only when the resolver reported success, Ralph staged every
    previously-conflicted path, no conflict marker survived, no
    unmerged path remains, and the deterministic merge commit landed.
    The resolver is fully contained: an exception is logged and treated
    as failure so the caller can abort the merge and keep the run
    alive.
    """
    conflicted = unmerged_paths(root)
    if not conflicted or _UNMERGED_QUERY_FAILED in conflicted:
        logger.warning(
            "auto_integrate: no readable conflicted paths to resolve: {}",
            conflicted,
        )
        return False
    try:
        resolved = bool(resolver(root, target))
    except Exception as resolver_exc:
        logger.warning(
            "auto_integrate: conflict resolver raised: {}", resolver_exc
        )
        return False
    if not resolved:
        return False
    return _stage_verify_and_commit(root, conflicted)


def _stage_verify_and_commit(root: Path, conflicted: list[str]) -> bool:
    """Stage the conflicted paths, prove the resolution, commit the merge.

    Staging is scoped to exactly the paths that were unmerged BEFORE
    the resolver ran — never ``git add -A`` — so an unrelated file the
    agent touched is not swept into the merge commit. The marker scan
    runs AFTER staging on purpose: ``git add`` clears the unmerged bit,
    so the textual scan is the only remaining proof of a real
    resolution. The git-authoritative unmerged check is retained as a
    second gate before the deterministic commit.
    """
    if not stage_paths(root, conflicted):
        logger.warning(
            "auto_integrate: failed to stage resolved paths: {}", conflicted
        )
        return False
    marked = paths_with_conflict_markers(root, conflicted)
    if marked:
        logger.warning(
            "auto_integrate: conflict markers remain after resolution: {}",
            marked,
        )
        return False
    remaining = unmerged_paths(root)
    if remaining:
        logger.warning(
            "auto_integrate: conflicts remain after resolution: {}", remaining
        )
        return False
    if not commit_merge_in_progress(root):
        logger.warning("auto_integrate: resolved merge failed to commit")
        return False
    return True


def _abort_merge_safely(root: Path) -> None:
    """Abort any in-progress merge; never raises."""
    try:
        abort_merge(root)
    except Exception as abort_exc:
        logger.warning("auto_integrate: abort_merge failed: {}", abort_exc)
