"""Rebase-then-endpoint-merge engine driver for auto-integration.

Extracted from :mod:`ralph.pipeline.auto_integrate` so that module stays
under the repo-structure ``_MAX_FILE_LINES`` cap, the same reason the
durable record helpers moved to
:mod:`ralph.pipeline.auto_integrate_record` and the outcome branch table
to :mod:`ralph.pipeline.auto_integrate_outcome`.

Everything here belongs to one phase of an integration attempt: run the
rebase engine, and when it conflicts or fails, abort it cleanly and fall
back to a single endpoint three-way merge (optionally agent-resolved).
The caller owns the phases either side of it -- the durable crash
record, the fast-forward, and the retry loop.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from loguru import logger

from ralph.git.rebase.rebase import (
    RebaseConflicts,
    RebaseFailed,
    RebaseNoOp,
    RebaseSuccess,
    abort_rebase,
    rebase_in_progress,
    rebase_onto,
)
from ralph.pipeline.auto_integrate_outcome import (
    record_conflict,
    record_rebase_outcome,
)
from ralph.pipeline.auto_integrate_record import clear_record
from ralph.pipeline.auto_integrate_resolve import (
    RESOLUTION_FAILED,
    endpoint_merge_with_resolution,
)

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.git.merge import MergeResult
    from ralph.pipeline.auto_integrate_resolve import ConflictResolver
    from ralph.pipeline.rebase_state import RebaseState

#: ``RebaseNoOp`` reason recorded when the bounded retry deliberately
#: skips ``rebase_onto`` to preserve the previous attempt's merge commit.
REBASE_SKIPPED_FOR_MERGE = (
    "rebase skipped on retry to preserve the integration merge commit"
)

__all__ = [
    "REBASE_SKIPPED_FOR_MERGE",
    "RebaseRunResult",
    "run_rebase_or_merge",
]


@dataclass(frozen=True)
class RebaseRunResult:
    """Outcome of :func:`run_rebase_or_merge`.

    On the happy path ``short_circuit`` is ``None`` and
    ``rebase_outcome`` carries the engine result so the caller can
    build the final :class:`RebaseState`. On a conflict / failure
    that has already been cleared from the durable record,
    ``short_circuit`` carries the :class:`RebaseState` the caller
    should return directly.
    """

    rebase_outcome: RebaseSuccess | RebaseConflicts | RebaseNoOp | RebaseFailed
    merge_attempted: bool
    merge_outcome: MergeResult | None
    short_circuit: RebaseState | None


def run_rebase_or_merge(
    root: Path,
    target: str,
    conflict_resolver: ConflictResolver | None,
    *,
    prefer_merge: bool = False,
) -> RebaseRunResult:
    """Drive rebase_onto, fall back to endpoint merge on conflict or failure.

    On success returns a ``RebaseRunResult`` with ``short_circuit``
    ``None`` and the ``rebase_outcome`` / ``merge_outcome`` the
    caller uses to build the final :class:`RebaseState`. Both a
    conflicted AND a failed rebase fall back to the endpoint merge —
    a rebase that fails for any reason must never end the integration
    attempt while a single three-way merge could still land it. When
    aborting a conflicted rebase leaves it in progress, the durable crash
    record is retained so startup recovery can restore the repository.

    With ``prefer_merge`` the rebase is skipped entirely and the
    endpoint merge runs directly. That flag is set ONLY by the retry
    loop, and only after an attempt that produced a merge commit: a
    plain ``git rebase`` (no ``--rebase-merges``) would drop that merge
    and replay the raw feature commits back into the conflict the
    resolver just settled.
    """
    if prefer_merge:
        return _endpoint_merge_result(
            root,
            target,
            RebaseNoOp(REBASE_SKIPPED_FOR_MERGE),
            conflict_resolver,
        )
    rebase_outcome = rebase_onto(target, repo_root=root)
    if not isinstance(rebase_outcome, (RebaseConflicts, RebaseFailed)):
        return RebaseRunResult(
            rebase_outcome=rebase_outcome,
            merge_attempted=False,
            merge_outcome=None,
            short_circuit=None,
        )

    return _fallback_to_endpoint_merge(
        root, target, rebase_outcome, conflict_resolver
    )


def _fallback_to_endpoint_merge(
    root: Path,
    target: str,
    rebase_outcome: RebaseConflicts | RebaseFailed,
    conflict_resolver: ConflictResolver | None,
) -> RebaseRunResult:
    """Abort the unfinished rebase and attempt the endpoint merge (AC-06/AC-07).

    With a ``conflict_resolver``, a conflicted endpoint merge is
    handed to the resolver and — on full resolution — committed
    deterministically, so the integration continues to the
    fast-forward phase instead of giving up (see
    :mod:`ralph.pipeline.auto_integrate_resolve`).
    """
    _abort_rebase_after_conflict(root)
    if rebase_in_progress(root):
        # Keep the pre-mutation record: abort_rebase can fail after the
        # rebase engine has created state, and recovery needs that record
        # to prove ownership and retry the abort/reset on the next run.
        return RebaseRunResult(
            rebase_outcome=rebase_outcome,
            merge_attempted=False,
            merge_outcome=None,
            short_circuit=record_conflict(
                reason="rebase in-progress after abort", target=target
            ),
        )

    return _endpoint_merge_result(root, target, rebase_outcome, conflict_resolver)


def _endpoint_merge_result(
    root: Path,
    target: str,
    rebase_outcome: RebaseConflicts | RebaseFailed | RebaseNoOp,
    conflict_resolver: ConflictResolver | None,
) -> RebaseRunResult:
    """Run the endpoint merge and map its outcome onto one branch table.

    Shared by the rebase-conflict fallback and the merge-only retry so
    both paths keep exactly one branch table; ``rebase_outcome`` is the
    marker each caller wants preserved for ``_classify_rebase_outcome``.
    """
    merge_result = endpoint_merge_with_resolution(
        root, target, conflict_resolver
    )
    if merge_result is None:
        # The merge attempt raised; surface that as the headline
        # conflict state (the merge-attempt reason is more
        # informative than the generic "both conflicted" message).
        clear_record(root)
        return RebaseRunResult(
            rebase_outcome=rebase_outcome,
            merge_attempted=True,
            merge_outcome=None,
            short_circuit=record_conflict(
                reason="rebase conflict followed by merge attempt exception",
                target=target,
            ),
        )
    if merge_result.outcome in ("conflict", RESOLUTION_FAILED):
        clear_record(root)
        return RebaseRunResult(
            rebase_outcome=rebase_outcome,
            merge_attempted=True,
            merge_outcome=merge_result,
            short_circuit=record_rebase_outcome(
                rebase_outcome=rebase_outcome,
                merge_attempted=True,
                merge_outcome=merge_result,
                target=target,
            ),
        )

    # The RebaseConflicts / RebaseFailed marker is preserved so
    # _classify_rebase_outcome can distinguish the AC-06
    # rebase-conflicted-then-cleanly-merged path (headline: merged)
    # from the AC-05 plain-rebase path (headline: rebased). Both
    # conflicted paths return earlier, so this tail is reached only
    # when merge_outcome.outcome is in {"success", "noop"} and the
    # classifier returns the "merged" headline, not "conflict".
    return RebaseRunResult(
        rebase_outcome=rebase_outcome,
        merge_attempted=True,
        merge_outcome=merge_result,
        short_circuit=None,
    )


def _abort_rebase_after_conflict(root: Path) -> None:
    """Abort a conflicted rebase; never raises."""
    try:
        if rebase_in_progress(root):
            abort_rebase(repo_root=root)
    except Exception as abort_exc:
        logger.warning("auto_integrate: abort_rebase failed: {}", abort_exc)
