"""Rebase-then-endpoint-merge engine driver for auto-integration.

Extracted from :mod:`ralph.pipeline.auto_integrate` so that module stays
under the repo-structure ``_MAX_FILE_LINES`` cap, the same reason the
durable record helpers moved to
:mod:`ralph.pipeline.auto_integrate_record` and the outcome branch table
to :mod:`ralph.pipeline.auto_integrate_outcome`.

Everything here belongs to one phase of an integration attempt: run the
rebase engine; when it conflicts, resolve it IN PLACE stop by stop --
Ralph stages each proved-resolved stop and runs ``git rebase
--continue``, leaving linear history and no merge commit -- and only
when that resolution declines, exhausts a budget, errors or is
unavailable abort the rebase cleanly and fall back to a single endpoint
three-way merge (itself optionally agent-resolved). The caller owns the
phases either side of it -- the durable crash record, the fast-forward,
and the retry loop.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from loguru import logger

from ralph.git.merge import MERGE_STATE_NONE, merge_state
from ralph.git.rebase.rebase import (
    RebaseConflicts,
    RebaseFailed,
    RebaseNoOp,
    RebaseSuccess,
    abort_rebase,
    rebase_in_progress,
    rebase_onto,
)
from ralph.git.subprocess_runner import run_git
from ralph.pipeline.auto_integrate_outcome import (
    record_conflict,
    record_rebase_outcome,
)
from ralph.pipeline.auto_integrate_record import clear_record, set_resolving_rebase
from ralph.pipeline.auto_integrate_recovery import (
    TerminalStateViolationError,
    post_attempt_verify,
)
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
REBASE_SKIPPED_FOR_MERGE = "rebase skipped on retry to preserve the integration merge commit"

#: Reasons the feature is being routed straight to the endpoint merge
#: instead of ``rebase_onto`` first. Recorded in the operator log
#: alongside the ``merged`` headline so the operator can tell a
#: prescribed rebase-skipping from a fallback-after-failure.
#:
#: * B3 — merge commits in the replay range are flattened and dropped
#:   by a plain ``git rebase``; routing to the endpoint merge
#:   preserves them. ``rebase --rebase-merges`` exists, but its
#:   side-effects on history topology and the documented edge cases
#:   make it unsuitable for the always-on auto-integration path.
#: * B7 — root commits have no parent to rebase onto. ``rebase
#:   --root`` exists, but the spec scopes it out of automation
#:   (B7) because of the operator-clarity cost of replaying
#:   history.
#: * B4/B6 — an all-empty replay (every commit became empty on
#:   replay because the change is already upstream) is the case
#:   the rebase ``--empty=drop`` flag was supposed to absorb.
#:   Older gits that ignore the flag abandon to endpoint merge
#:   anyway; the routing here short-circuits the loop and
#:   still lands the branch via the merge fallback.
_REASON_MERGE_COMMITS = "branch contains merge commits; routing to endpoint merge"
_REASON_ROOT_COMMITS = "branch contains root commits; routing to endpoint merge"
_REASON_ALL_EMPTY = "branch has no replayable commits; routing to endpoint merge"

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
    """Drive rebase_onto, resolving conflicts in place before any fallback.

    On success returns a ``RebaseRunResult`` with ``short_circuit``
    ``None`` and the ``rebase_outcome`` / ``merge_outcome`` the
    caller uses to build the final :class:`RebaseState`. A rebase that
    FAILS, and a conflicted rebase whose in-place resolution does not
    land, both fall back to the endpoint merge —
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

    Before ``rebase_onto`` is called the function also detects ranges
    a plain rebase would mishandle (B3 merge commits, B7 root commits,
    B4/B6 all-empty replays) and routes them straight to the endpoint
    merge so the rebase engine never silently flattens a merge
    resolution, replays an already-upstream patch into a conflict, or
    drops a root commit. The rebase ``--empty=drop`` flag from step 2
    still lands the all-empty case on modern git; this routing is the
    backstop for older git that ignores the flag.

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
    # Pre-flight routing: detect ranges a plain ``rebase --onto``
    # would silently mishandle and skip the rebase phase entirely.
    # The rebase result is replaced with a ``RebaseNoOp`` carrying
    # the routing reason so the operator log line tells the operator
    # WHY the merge was chosen; the headline ``merged`` comes from
    # the endpoint merge that follows.
    routing_reason = _range_routing_reason(root, target)
    if routing_reason is not None:
        logger.info("auto_integrate: {} (target '{}')", routing_reason, target)
        return _endpoint_merge_result(
            root,
            target,
            RebaseNoOp(routing_reason),
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
        resolved = _resolve_conflicted_rebase(root, target, rebase_stop_resolver, display)
        if resolved is not None:
            return resolved

    return _fallback_to_endpoint_merge(root, target, rebase_outcome, conflict_resolver)


def _range_routing_reason(root: Path, target: str) -> str | None:
    """Decide whether the feature range must skip ``rebase_onto``.

    Returns the routing reason (``_REASON_*``) the operator log line
    should carry, or ``None`` to run the rebase. The check is
    index-/ref-queries only; no git mutation, so a routing decision
    that turns out wrong is reversible by retrying with
    ``prefer_merge=False`` next seam.

    Each predicate is a cheap ``rev-list`` over ``<target>..HEAD``:

    * **B3** — ``rev-list --merges``: nonzero means a merge commit
      sits in the range; a plain ``rebase`` would drop it. Route to
      endpoint merge so the resolution the agent was paid to produce
      survives the integration.
    * **B7** — ``rev-list --max-parents=0``: nonzero means a root
      commit sits in the range; ``rebase --root`` is out of scope
      (B7). Route to endpoint merge.
    * **B4/B6** — an all-empty replay (every commit becomes empty
      on replay because the change is already upstream) is the
      case the rebase ``--empty=drop`` flag was supposed to absorb
      (B4). Modern git honors the flag; older git abandons.
      Detection: ``rev-list --count <target>..HEAD`` is the total
      number of feature commits; ``rev-list --cherry-pick
      --no-merges <target>..HEAD`` is the number of commits the
      replay would actually need to apply. When they are equal
      and the replay-range is non-empty, every commit was
      already applied, so the rebase would walk through zero
      meaningful stops and the merge is the only path that
      moves the ref. The endpoint merge is the B4/B6 backstop.
    """
    # Total feature commits in the range; zero means there is
    # nothing to rebase OR route — the precondition check should
    # have caught the no-commits case earlier, but we still fail
    # open to ``rebase_onto`` so the existing NoOp machinery reports
    # it. ``None`` (the topology query failed) also falls through so
    # the rebase engine's own classification is the final answer —
    # never a routing guess based on an unanswerable query.
    total = _rev_list_count(root, target, extra_args=())
    if total is None or total == 0:
        return None
    merges = _rev_list_count(root, target, extra_args=("--merges",))
    if merges is not None and merges > 0:
        return _REASON_MERGE_COMMITS
    roots = _rev_list_count(root, target, extra_args=("--max-parents=0",))
    if roots is not None and roots > 0:
        return _REASON_ROOT_COMMITS
    if _all_empty_replay(root, target, total):
        return _REASON_ALL_EMPTY
    return None


def _rev_list_count(root: Path, target: str, *, extra_args: tuple[str, ...]) -> int | None:
    """Run ``git rev-list <args> <target>..HEAD`` and return the count.

    Returns ``None`` when the query failed (non-zero exit or
    malformed output) so the caller can treat the topology as
    UNKNOWN rather than the literal "zero commits" case the old
    implementation returned. Topology queries are guarded by the
    universal per-call timeout and the non-interactive env baseline
    (D1/D15), so a non-zero exit is genuine: a deleted target ref
    (E4), a corrupt object store (H5), or a sibling agent holding a
    lock (A10/E9). Failing closed by treating those as
    "unanswerable" routes through the rebase engine's own
    classification rather than silently merging when the rebase
    would have been the correct path -- the exact bug class that
    would silently flatten a merge commit (B3) or replay an
    already-upstream patch into a conflict (B6).
    """
    result = run_git(
        ("rev-list", "--count", *extra_args, "--", f"{target}..HEAD"),
        cwd=root,
        label="auto-integrate:rev-list-count",
    )
    if result.returncode != 0:
        logger.warning(
            "auto_integrate: rev-list {} for {}..HEAD exited {}: {}; "
            "treating topology as unknown (will run the rebase)",
            extra_args,
            target,
            result.returncode,
            (result.stderr or result.stdout).strip()[:200],
        )
        return None
    try:
        return int(result.stdout.strip())
    except ValueError:
        logger.warning(
            "auto_integrate: rev-list {} for {}..HEAD returned non-integer "
            "stdout {!r}; treating topology as unknown",
            extra_args,
            target,
            result.stdout.strip()[:200],
        )
        return None


def _all_empty_replay(root: Path, target: str, feature_count: int) -> bool:
    """True when every feature commit is already on ``target`` upstream.

    The cherry-pick walker in ``rev-list`` drops commits whose
    patch-id matches one already in the upstream history (B6). A
    non-empty feature range with an EMPTY cherry-picked range is
    therefore the B4 all-empty-replay case: every commit was
    already applied, so the rebase will walk through zero
    meaningful stops regardless of ``--empty=drop``. The endpoint
    merge is the only path that lands the branch.

    ``feature_count`` is the total commit count already proven by
    the caller; a separate re-query is redundant and would
    re-introduce the topology-error hole the ``_rev_list_count``
    return-type change closed. ``None`` from the cherry-pick walker
    means the topology query failed: routing on an unanswerable
    question is exactly the bug this whole change is about, so the
    walker returning ``None`` is treated as "not all-empty" and
    the rebase engine takes over.
    """
    if feature_count == 0:
        return False
    cherry_count = _rev_list_count(root, target, extra_args=("--cherry-pick", "--no-merges"))
    if cherry_count is None:
        return False
    return cherry_count == 0


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

    The resolution does not start until the durable record actually says
    ``resolving_rebase=true``. A resolution session runs for as long as an
    agent takes, and if this run is killed inside that window, startup
    recovery reads the record to decide what it found; a record still
    saying ``false`` makes an interrupted resolution indistinguishable
    from an ordinary crashed rebase, and the operator loses the one
    warning that explains it. When the flag cannot be persisted the
    rebase is therefore handed to the fallback -- which aborts it here
    and now, while this process is alive to do it -- rather than left
    paused under an agent whose crash could not be described.
    """
    if not set_resolving_rebase(root, True):
        logger.warning(
            "auto_integrate: could not record the in-flight rebase resolution "
            "for '{}'; falling back to the endpoint merge",
            target,
        )
        return None
    try:
        with conflict_status_bar_session(display, root):
            resolved = resolve_rebase_in_progress(root, target, rebase_stop_resolver)
    finally:
        # The unflag cannot fail the integration -- the resolution has
        # already happened by the time it runs -- but a stale ``true``
        # left behind would make the NEXT unrelated crash report an
        # interrupted resolution that never existed, so say so.
        if not set_resolving_rebase(root, False):
            logger.warning(
                "auto_integrate: could not clear the rebase-resolution flag "
                "for '{}'; the durable record may misreport a later crash as "
                "an interrupted resolution",
                target,
            )
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

    R6/AC-06: runs :func:`post_attempt_verify` BEFORE the endpoint
    merge so a rebase abort that left in-progress markers is
    surfaced loudly before the merge starts. The merge itself
    runs through the same invariant via :func:`_endpoint_merge_result`
    on its conflict / resolution paths. The ``owns_resolution``
    flag is ``True`` when the conflict resolver is mid-edit.
    """
    _abort_rebase_after_conflict(root)
    if rebase_in_progress(root):
        # Keep the pre-mutation record: abort_rebase can fail after the
        # rebase engine has created state, and recovery needs that record
        # to prove ownership and retry the abort/reset on the next run.
        _verify_terminal_state(
            root, expected_head_sha=None, owns_resolution=conflict_resolver is not None
        )
        return RebaseRunResult(
            rebase_outcome=rebase_outcome,
            merge_attempted=False,
            merge_outcome=None,
            short_circuit=record_conflict(reason="rebase in-progress after abort", target=target),
        )
    _verify_terminal_state(
        root, expected_head_sha=None, owns_resolution=conflict_resolver is not None
    )

    return _endpoint_merge_result(root, target, rebase_outcome, conflict_resolver)


def _verify_terminal_state(
    root: Path,
    *,
    expected_head_sha: str | None,
    owns_resolution: bool,
) -> None:
    """Run :func:`post_attempt_verify` on a merge / fallback exit path.

    Catches :class:`TerminalStateViolationError` and logs a loud
    ``ERROR`` -- the caller continues to record its outcome (a
    merge / fallback can legitimately finish with a feature that
    already moved, so the only invariant we can check here is the
    absence of stale in-progress markers). Extracted into its
    own helper so the three call sites in this module share one
    diagnostic shape.
    """
    try:
        post_attempt_verify(
            root,
            expected_head_sha=expected_head_sha,
            owns_resolution=owns_resolution,
        )
    except TerminalStateViolationError as exc:
        logger.error(
            "auto_integrate_rebase_merge: terminal-state invariant "
            "violation: {}; the recovery preamble will reclaim this on "
            "the next run, and the durable record is RETAINED so the "
            "next recovery can still restore the pre-attempt state. "
            "The operator should be aware the merge / fallback path "
            "leaked state.",
            exc,
        )


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
    merge_result = endpoint_merge_with_resolution(root, target, conflict_resolver)
    if merge_result is None:
        # The merge attempt raised; surface that as the headline
        # conflict state (the merge-attempt reason is more
        # informative than the generic "both conflicted" message).
        _clear_record_if_no_inflight_op(root)
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
        _clear_record_if_no_inflight_op(root)
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


def _clear_record_if_no_inflight_op(root: Path) -> None:
    """Clear the durable record ONLY when no in-progress operation remains.

    Terminal-state invariant: clearing the record while ``MERGE_HEAD``
    or rebase bookkeeping is still on disk ORPHANS that state --
    startup recovery reconciles only operations whose record it holds,
    so the leftover markers fail ``check_rebase_preconditions`` at
    every later seam with nothing ever able to clean them. When the
    proof of cleanliness cannot be obtained the record is retained:
    a spurious retention costs one redundant recovery pass at next
    startup, a spurious clear costs the worktree its integration
    forever.
    """
    try:
        clean = merge_state(root) == MERGE_STATE_NONE and not rebase_in_progress(root)
    except Exception as state_exc:
        logger.warning(
            "auto_integrate: could not prove no in-flight operation remains"
            " in {} ({}); retaining the durable record for startup recovery",
            root,
            state_exc,
        )
        return
    if not clean:
        logger.warning(
            "auto_integrate: in-progress merge/rebase state remains in {}"
            " after a failed integration; retaining the durable record so"
            " startup recovery can reconcile it",
            root,
        )
        return
    clear_record(root)


def _abort_rebase_after_conflict(root: Path) -> None:
    """Abort a conflicted rebase; never raises."""
    try:
        if rebase_in_progress(root):
            abort_rebase(repo_root=root)
    except Exception as abort_exc:
        logger.warning("auto_integrate: abort_rebase failed: {}", abort_exc)


# ----- AC-14 catalog evidence -----
# This file is the authoritative source for the catalog entries listed
# below. Each ``# AC-14 rationale: <ID>`` line is the code-adjacent
# marker the AC-14 audit looks for; each ``# ladder rung: <N>``
# names the rung the entry sits on. Adding a new entry here requires
# BOTH lines or the audit fails.

# AC-14 rationale: B3
# ladder rung: 3
# AC-14 rationale: B6
# ladder rung: 3
# AC-14 rationale: B7
# ladder rung: 3
# AC-14 rationale: D11
# ladder rung: 3
# ----- end AC-14 catalog evidence -----
