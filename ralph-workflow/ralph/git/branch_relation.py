
from __future__ import annotations

from enum import StrEnum
from pathlib import Path

from loguru import logger

from ralph.git import merge as merge_module
from ralph.git import merge_obstructions
from ralph.git.rebase import rebase as rebase_module


class BranchRelation(StrEnum):

    SYNCED = "synced"
    TARGET_AHEAD = "target_ahead"
    FEATURE_AHEAD = "feature_ahead"
    DIVERGED = "diverged"
    UNKNOWN = "unknown"

    @property
    def needs_catch_up(self) -> bool:
        return self in {
            BranchRelation.TARGET_AHEAD,
            BranchRelation.DIVERGED,
            BranchRelation.UNKNOWN,
        }

    @property
    def needs_landing(self) -> bool:
        return self in {
            BranchRelation.FEATURE_AHEAD,
            BranchRelation.DIVERGED,
            BranchRelation.UNKNOWN,
        }


def _operation_in_flight(root: Path) -> bool:
    try:
        return rebase_module.rebase_in_progress(root) or merge_module.merge_state(root) != (
            merge_module.MERGE_STATE_NONE
        )
    except Exception as exc:
        logger.warning("operation-in-flight probe for {} failed: {}", root, exc)
        return True


def branch_relation(repo_root: Path | str, target: str, feature: str = "HEAD") -> BranchRelation:
    if _operation_in_flight(Path(repo_root)):
        return BranchRelation.UNKNOWN
    target_in_feature = merge_obstructions.ancestry_state(repo_root, target, feature)
    feature_in_target = merge_obstructions.ancestry_state(repo_root, feature, target)
    if target_in_feature is None or feature_in_target is None:
        return BranchRelation.UNKNOWN
    if target_in_feature and feature_in_target:
        return BranchRelation.SYNCED
    if target_in_feature:
        return BranchRelation.FEATURE_AHEAD
    if feature_in_target:
        return BranchRelation.TARGET_AHEAD
    return BranchRelation.DIVERGED


__all__ = ["BranchRelation", "branch_relation"]
