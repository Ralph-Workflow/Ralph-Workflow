"""Agent-assisted conflict resolution for the auto-integrate endpoint merge.

When the endpoint merge of the mainline into the feature branch
conflicts, the pipeline can hand the conflicted working tree to a
conflict resolver — in production a focused dev-agent invocation whose
ONLY job is to resolve the conflict markers and stage the results. The
merge commit itself is always created deterministically by
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
    unmerged_paths,
)

#: Signature of a conflict resolver: ``(repo_root, target_branch) ->
#: resolved``. Returning True means every conflict was resolved and
#: staged; the caller then commits the merge deterministically.
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

    True only when the resolver reported success, no unmerged paths
    remain, and the deterministic merge commit landed. The resolver is
    fully contained: an exception is logged and treated as failure so
    the caller can abort the merge and keep the run alive.
    """
    try:
        resolved = bool(resolver(root, target))
    except Exception as resolver_exc:
        logger.warning(
            "auto_integrate: conflict resolver raised: {}", resolver_exc
        )
        return False
    if not resolved:
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
