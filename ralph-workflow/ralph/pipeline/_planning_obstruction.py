
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

    NONE = "none"
    LOCAL_OVERWRITE = "local_overwrite"
    UNTRACKED_OVERWRITE = "untracked_overwrite"
    STAGED_BLOCKS_INTEGRATION = "staged_blocks_integration"
    TRACKED_CHANGES_BLOCKS_INTEGRATION = "tracked_changes_blocks_integration"


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
    if readiness.block_kinds:
        return PlanningObstructionKind.NONE
    reasons = readiness.blocking_reasons
    if not reasons:
        return None
    return _READINESS_REASON_KINDS.get(reasons[0])


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
