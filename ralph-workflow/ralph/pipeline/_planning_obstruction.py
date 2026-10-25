"""Closed vocabulary for routing planning commit through dirty worktrees.

The run loop's :func:`_route_planning_commit` must decide whether to
route a planning commit through a policy cleanup phase. The decision
used to be derived from five prose substrings against
``state.rebase.last_reason``. That coupling was word-brittle -- a
rewording of the reason could silently turn the routing on or off --
and it forced the run loop to parse free-form text.

The typed seam lives here. The closed :class:`PlanningObstructionKind`
enum is the producer-side vocabulary, the :func:`identify_planning_obstruction`
helper is the only place that translates a free-form reason into a kind,
and the run loop reads the kind from
:attr:`RebaseState.last_planning_obstruction`. The helper is the ONLY
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

__all__ = ["PlanningObstructionKind", "identify_planning_obstruction"]


class PlanningObstructionKind(StrEnum):
    """Closed set of planning-obstruction kinds the run loop routes on."""

    NONE = "none"
    LOCAL_OVERWRITE = "local_overwrite"
    UNTRACKED_OVERWRITE = "untracked_overwrite"
    STAGED_BLOCKS_INTEGRATION = "staged_blocks_integration"
    TRACKED_CHANGES_BLOCKS_INTEGRATION = "tracked_changes_blocks_integration"


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
    """Translate a free-form planning reason into a typed kind.

    This helper is the ONLY place in the pipeline that parses a reason
    string for the planning-obstruction vocabulary. Downstream code
    reads only the typed kind from
    :attr:`RebaseState.last_planning_obstruction`; the run loop never
    sees this prose.

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
