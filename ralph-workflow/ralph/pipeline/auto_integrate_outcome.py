"""Outcome classification for :mod:`ralph.pipeline.auto_integrate`.

Owns the single branch table that maps a rebase result plus an optional
endpoint-merge result onto the ``(action, reason)`` pair the runner
renders in its ``auto-integrate:`` line, and the thin
:class:`~ralph.pipeline.rebase_state.RebaseState` constructors built on
top of it.

Extracted from :mod:`ralph.pipeline.auto_integrate` to keep that module
under the repo-structure ``_MAX_FILE_LINES`` cap, the same way the
record helpers were extracted to
:mod:`ralph.pipeline.auto_integrate_record`. The classifiers are a
coherent unit -- one decision table, no git I/O, no callers outside the
auto-integration pipeline -- so the split follows a real seam rather
than a line count.

Every function here is pure: given the engine results it returns a
value and touches neither the filesystem nor a subprocess.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.git.rebase.rebase import (
    RebaseConflicts,
    RebaseFailed,
    RebaseNoOp,
    RebaseSuccess,
)
from ralph.pipeline.auto_integrate_resolve import RESOLUTION_FAILED
from ralph.pipeline.rebase_state import RebaseState

if TYPE_CHECKING:
    from ralph.git.merge import MergeResult

#: Outcome verbs recorded on ``RebaseState.last_action`` so the runner
#: can format the user-facing log line (rebased / merged /
#: skipped / conflict / recovered). The landing result is recorded
#: on the separate ``fast_forwarded`` boolean, not as an action verb.
ACTION_SKIPPED = "skipped"
ACTION_REBASED = "rebased"
ACTION_MERGED = "merged"
ACTION_CONFLICT = "conflict"
ACTION_RECOVERED = "recovered"


def record_skip(
    *,
    reason: str,
    target: str | None,
    fast_forwarded: bool = False,
) -> RebaseState:
    """Build a ``RebaseState`` recording a skip outcome."""
    return RebaseState(
        last_action=ACTION_SKIPPED,
        last_reason=reason,
        last_target=target,
        fast_forwarded=fast_forwarded,
    )


def record_conflict(
    *,
    reason: str,
    target: str | None,
) -> RebaseState:
    """Build a ``RebaseState`` recording a conflict outcome (AC-07)."""
    return RebaseState(
        last_action=ACTION_CONFLICT,
        last_reason=reason,
        last_target=target,
        fast_forwarded=False,
    )


def record_rebase_outcome(
    *,
    rebase_outcome: RebaseSuccess | RebaseConflicts | RebaseNoOp | RebaseFailed,
    merge_attempted: bool,
    merge_outcome: MergeResult | None,
    target: str,
) -> RebaseState:
    """Build a ``RebaseState`` from the rebase + optional merge result.

    Success path -> ``last_action='rebased'`` or ``'merged'`` and
    ``fast_forwarded`` is set by the caller after the fast-forward
    phase; the function leaves ``fast_forwarded=False`` here so the
    caller can update it once the CAS / worktree-ff step completes.

    The mapping is centralized in :func:`classify_rebase_outcome`
    so this function is a thin constructor over the (action, reason)
    pair it returns.
    """
    action, reason = classify_rebase_outcome(
        rebase_outcome=rebase_outcome,
        merge_attempted=merge_attempted,
        merge_outcome=merge_outcome,
    )
    return RebaseState(
        last_action=action,
        last_reason=reason,
        last_target=target,
        fast_forwarded=False,
    )


def _classify_rebase_conflict_outcome(
    *,
    merge_attempted: bool,
    merge_outcome: MergeResult | None,
) -> tuple[str, str | None] | None:
    """Sub-classifier for the ``RebaseConflicts`` branch.

    Returns ``None`` when this sub-classifier cannot decide (e.g.
    the merge was not attempted at all and we still want the
    generic "rebase conflicts" headline to be raised by the caller).
    Otherwise returns the ``(action, reason)`` pair to use as the
    :func:`record_rebase_outcome` headline.
    """
    if not merge_attempted or merge_outcome is None:
        return None
    # The rebase conflicted; the endpoint merge that followed
    # is recorded in ``merge_outcome.outcome``. A conflicting
    # merge is the AC-07 "both conflicted" case (headline:
    # conflict). A clean success/noop merge is the AC-06
    # "rebase conflicted but endpoint merge succeeded" case
    # (headline: merged) -- the rebase-apply/rebase-merge state
    # was already aborted by ``_resolve_rebase_conflict`` so the
    # resulting branch state is the merged tree.
    if merge_outcome.outcome == RESOLUTION_FAILED:
        return ACTION_CONFLICT, "conflict resolution failed; merge aborted"
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
    """Map a rebase + optional merge result to a ``(action, reason)`` pair.

    Split out from :func:`record_rebase_outcome` to keep the
    headline builder within the ruff PLR0911 return-statement cap
    (the builder is a single-return constructor; this classifier
    owns the branch table).
    """
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
    # RebaseSuccess or after a clean endpoint merge.
    if merge_attempted and merge_outcome is not None:
        return ACTION_MERGED, None
    return ACTION_REBASED, None


def _classify_rebase_noop_outcome(
    *,
    rebase_outcome: RebaseNoOp,
    merge_attempted: bool,
    merge_outcome: MergeResult | None,
) -> tuple[str, str | None]:
    """Sub-classifier for the ``RebaseNoOp`` branch.

    A no-op with no merge behind it is recorded as ``rebased`` (no work
    done, but the branch is already aligned with the target). A no-op
    that DID run a merge is the merge-only retry path, where
    ``rebase_onto`` was deliberately skipped to preserve the previous
    attempt's merge commit -- the headline there belongs to the merge,
    not to a phantom "rebased".

    Split out so :func:`classify_rebase_outcome` stays within the ruff
    PLR0911 return-statement cap.
    """
    if merge_attempted:
        return classify_merge_only_outcome(merge_outcome)
    return ACTION_REBASED, rebase_outcome.reason


def classify_merge_only_outcome(
    merge_outcome: MergeResult | None,
) -> tuple[str, str | None]:
    """Classify an attempt that went straight to the endpoint merge.

    Used by the bounded retry after a merge-producing attempt, where
    ``rebase_onto`` is deliberately skipped so the previous attempt's
    merge commit survives. No rebase ran, so none of the
    rebase-flavoured headlines apply.
    """
    if merge_outcome is None:
        return ACTION_CONFLICT, "endpoint merge attempt raised"
    if merge_outcome.outcome == RESOLUTION_FAILED:
        return ACTION_CONFLICT, "conflict resolution failed; merge aborted"
    if merge_outcome.outcome in {"success", "noop"}:
        return ACTION_MERGED, None
    return ACTION_CONFLICT, f"endpoint merge conflicted ({merge_outcome.outcome})"


def _classify_rebase_failed_outcome(
    *,
    rebase_outcome: RebaseFailed,
    merge_attempted: bool,
    merge_outcome: MergeResult | None,
) -> tuple[str, str | None]:
    """Sub-classifier for the ``RebaseFailed`` branch.

    The endpoint-merge fallback runs for failed rebases too; the same
    sub-table applies, with the failed-rebase wording substituted into
    the merge-conflicted headline and a skip when no merge ran.
    """
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
    "record_skip",
]
