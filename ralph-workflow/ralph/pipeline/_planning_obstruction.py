"""Closed vocabulary for routing planning commit through dirty worktrees.

The run loop's :func:`_route_planning_commit` must decide whether to
route a planning commit through a policy cleanup phase. The decision
used to be derived from five prose substrings against
``state.rebase.last_reason``. That coupling was word-brittle -- a
rewording of the reason could silently turn the routing on or off --
and it forced the run loop to parse free-form text.

The typed seam lives here. The closed :class:`PlanningObstructionKind`
enum is the routing vocabulary, recorded on
:attr:`RebaseState.last_planning_obstruction` by the producer-side
:func:`planning_obstruction_from_readiness` (which reads only typed
readiness fields and exact-matches the canonical readiness reasons --
never a prose scan). The run loop reads the kind from the record and
falls back to :func:`identify_planning_obstruction` ONLY for legacy
checkpoints that predate the typed field. The helper is the ONLY
allowed prose-substring scan in the pipeline: every other consumer
reads the typed kind.

Members
-------

* :attr:`NONE` -- the planning commit does not face an obstruction;
  the run loop falls through to ordinary routing.
* :attr:`LOCAL_OVERWRITE` -- a clean ``git pull --ff-only`` would
  overwrite a local change; the run loop routes through a commit
  cleanup phase to capture the work before the pull.
* :attr:`UNTRACKED_OVERWRITE` -- an untracked working tree file
  would be clobbered by the pull.
* :attr:`STAGED_BLOCKS_INTEGRATION` -- the worktree has staged
  uncommitted work that blocks a forced integration; the planning
  seam must call the cleanup phase to commit or unstage the work
  before integration can proceed.
* :attr:`TRACKED_CHANGES_BLOCKS_INTEGRATION` -- the worktree has unstaged
  or modified tracked changes that block integration; the planning
  seam must route through cleanup and commit before catch-up.
"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.pipeline.integration_resolution import TreeReadinessAssessment

__all__ = [
    "PlanningObstructionKind",
    "identify_planning_obstruction",
    "planning_obstruction_from_readiness",
]


class PlanningObstructionKind(StrEnum):
    """Closed set of planning-obstruction kinds the run loop routes on."""

    NONE = "none"
    LOCAL_OVERWRITE = "local_overwrite"
    UNTRACKED_OVERWRITE = "untracked_overwrite"
    STAGED_BLOCKS_INTEGRATION = "staged_blocks_integration"
    TRACKED_CHANGES_BLOCKS_INTEGRATION = "tracked_changes_blocks_integration"


#: Canonical uncommitted-work blocking reasons produced by
#: :func:`ralph.pipeline.integration_resolution.assess_tree_readiness`, mapped
#: EXACTLY (full-string equality, not substring scanning) onto the closed
#: vocabulary. The readiness producer owns these strings; an exact-match
#: table means a drift fails closed to "not typeable" instead of silently
#: mis-routing a reworded reason.
_READINESS_REASON_KINDS: dict[str, PlanningObstructionKind] = {
    "staged uncommitted work blocks integration": (
        PlanningObstructionKind.STAGED_BLOCKS_INTEGRATION
    ),
    "worktree not clean; uncommitted tracked changes deferred catch-up integration": (
        PlanningObstructionKind.TRACKED_CHANGES_BLOCKS_INTEGRATION
    ),
}


def planning_obstruction_from_readiness(
    readiness: TreeReadinessAssessment,
) -> PlanningObstructionKind | None:
    """Translate a typed readiness assessment into the closed vocabulary.

    This is the PRODUCER-side seam: boundary and planning deferrals call it
    at record time to carry the typed obstruction cause into the durable
    record alongside the prose. It reads only typed readiness fields plus
    the canonical uncommitted-work reasons (exact match) -- it never
    substring-scans free-form prose. That parsing job belongs solely to
    :func:`identify_planning_obstruction`, the legacy-checkpoint shim the
    run loop consults only when the typed field is absent.

    Returns:

    * a :class:`PlanningObstructionKind` member for every typed readiness
      state: the canonical uncommitted-work reasons map to their kinds, and
      proven-conflict block kinds (:class:`IntegrationBlockKind`) map to
      :attr:`PlanningObstructionKind.NONE` -- the planning-obstruction
      vocabulary has no member for proven conflicts, they refuse dispatch
      upstream so the run loop never routes on them, and the legacy shim
      maps their canonical prose to ``NONE`` as well, so the typed answer
      agrees with the shim for every proven-conflict reason;
    * ``None`` when the readiness cause is not one of the canonical typed
      states (e.g. prose replayed from an old durable record): the caller
      then leaves ``last_planning_obstruction`` unset so the record routes
      through the legacy shim exactly like an old checkpoint.
    """
    if readiness.block_kinds:
        # Proven conflicts (unmerged paths, rebase/merge in progress): the
        # closed vocabulary deliberately has no member for these -- they
        # refuse dispatch upstream, so the run loop never routes on them.
        return PlanningObstructionKind.NONE
    reasons = readiness.blocking_reasons
    if not reasons:
        return None
    return _READINESS_REASON_KINDS.get(reasons[0])


# Substring table is module-private. Every substring here must map to
# exactly one kind; every member of the enum must appear in the table
# unless its value is the empty set (NONE).
_LOCAL_OVERWRITE_SUBSTRINGS: tuple[str, ...] = (
    "local changes would be overwritten",
    "local changes to the following files would be overwritten",
)
_UNTRACKED_OVERWRITE_SUBSTRINGS: tuple[str, ...] = (
    "untracked files would be overwritten",
    "untracked working tree files would be overwritten",
)
_STAGED_BLOCKS_SUBSTRINGS: tuple[str, ...] = ("staged uncommitted work blocks integration",)
_TRACKED_CHANGES_BLOCKS_SUBSTRINGS: tuple[str, ...] = (
    "uncommitted tracked changes",
    "tracked changes block",
)


def identify_planning_obstruction(reason: str) -> PlanningObstructionKind:
    """LEGACY-CHECKPOINT SHIM: translate a free-form planning reason into a
    typed kind.

    New records do NOT pass through here: producers carry the typed kind
    into the durable record via :func:`planning_obstruction_from_readiness`
    at record time. This shim exists ONLY so checkpoints written before the
    typed field existed (``last_planning_obstruction`` absent) keep routing
    identically -- the run loop consults it only when the typed field is
    missing. It is the ONLY place in the pipeline that parses a reason
    string for the planning-obstruction vocabulary.

    The function is total: a ``None``, empty, or unrecognised reason
    collapses to :attr:`PlanningObstructionKind.NONE` so the run loop
    can rely on a closed-vocabulary value.
    """
    if not reason:
        return PlanningObstructionKind.NONE
    lowered = reason.lower()
    for needle in _STAGED_BLOCKS_SUBSTRINGS:
        if needle in lowered:
            return PlanningObstructionKind.STAGED_BLOCKS_INTEGRATION
    for needle in _TRACKED_CHANGES_BLOCKS_SUBSTRINGS:
        if needle in lowered:
            return PlanningObstructionKind.TRACKED_CHANGES_BLOCKS_INTEGRATION
    for needle in _LOCAL_OVERWRITE_SUBSTRINGS:
        if needle in lowered:
            return PlanningObstructionKind.LOCAL_OVERWRITE
    for needle in _UNTRACKED_OVERWRITE_SUBSTRINGS:
        if needle in lowered:
            return PlanningObstructionKind.UNTRACKED_OVERWRITE
    return PlanningObstructionKind.NONE
