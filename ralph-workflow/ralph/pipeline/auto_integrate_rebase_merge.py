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
from ralph.pipeline.auto_integrate_record import clear_record, set_resolving_rebase
from ralph.pipeline.auto_integrate_resolve import (
    RESOLUTION_FAILED,
    endpoint_merge_with_resolution,
)
from ralph.pipeline.conflict_resolution import resolve_rebase_in_progress
from ralph.pipeline.conflict_resolution.status import conflict_status_bar_session

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.display.parallel_display import ParallelDisplay
    from ralph.git.merge import MergeResult
    from ralph.pipeline.auto_integrate_resolve import ConflictResolver
    from ralph.pipeline.conflict_resolution import RebaseStopResolver
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
    rebase_stop_resolver: RebaseStopResolver | None = None,
    display: ParallelDisplay | None = None,
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

    With a ``rebase_stop_resolver`` a CONFLICTED rebase is resolved in
    place -- commit by commit, through ``git rebase --continue`` -- before
    the abort-and-endpoint-merge fallback is considered at all.
    ``display`` is used only to own the resolution footer for the whole
    loop; it is optional and never required for correctness.
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

    if isinstance(rebase_outcome, RebaseConflicts) and rebase_stop_resolver is not None:
        resolved = _resolve_conflicted_rebase(
            root, target, rebase_stop_resolver, display
        )
        if resolved is not None:
            return resolved

    return _fallback_to_endpoint_merge(
        root, target, rebase_outcome, conflict_resolver
    )


def _resolve_conflicted_rebase(
    root: Path,
    target: str,
    rebase_stop_resolver: RebaseStopResolver,
    display: ParallelDisplay | None,
) -> RebaseRunResult | None:
    """Resolve the paused rebase in place, or hand it back to the fallback.

    This is the branch the whole feature turns on. Before it existed a
    conflicted rebase was ALWAYS destroyed by
    :func:`_abort_rebase_after_conflict` on the first stop, so the
    rebase-conflict-resolution pipeline could only ever be reached for the
    follow-up endpoint merge -- never for the rebase the operator was
    actually watching fail.

    Returns a :class:`RebaseRunResult` shaped like a clean rebase when the
    replay completed, so the caller proceeds to the fast-forward and the
    operator sees ``rebased``. Returns ``None`` when the resolution
    declined, which routes the caller into the pre-existing
    abort-then-endpoint-merge path completely unchanged.

    The footer is captured once here and restored once, around the ENTIRE
    loop: the per-stop pushes happen inside it, so a per-stop capture
    would snapshot the conflict bar itself and strand it after the loop.
    """
    set_resolving_rebase(root, True)
    try:
        with conflict_status_bar_session(display, root):
            resolved = resolve_rebase_in_progress(root, target, rebase_stop_resolver)
    finally:
        set_resolving_rebase(root, False)
    if not resolved:
        logger.info(
            "auto_integrate: rebase conflict resolution declined for '{}'; "
            "falling back to the endpoint merge",
            target,
        )
        return None
    logger.info("auto_integrate: resolved the conflicted rebase onto '{}'", target)
    return RebaseRunResult(
        rebase_outcome=RebaseSuccess(),
        merge_attempted=False,
        merge_outcome=None,
        short_circuit=None,
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
