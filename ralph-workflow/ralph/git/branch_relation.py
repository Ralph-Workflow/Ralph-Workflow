"""How a feature tip and its integration target stand to each other.

This is the ONE place both ancestry directions are read and named. Every
gate and every "nothing to do" shortcut asks it, so the planning gate and
the integration seam can never answer opposite questions about the same
pair of branches. Probes are reached through their modules so a test can
substitute git's answers at the same seams the rest of integration uses.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path

from loguru import logger

from ralph.git import merge as merge_module
from ralph.git import merge_obstructions
from ralph.git.rebase import rebase as rebase_module


class BranchRelation(StrEnum):
    """How a feature tip and its integration target stand to each other.

    Two independent questions, so four known answers plus "not proven":
    is the target inside the feature (nothing to catch up) and is the
    feature inside the target (nothing to land)?
    """

    SYNCED = "synced"
    TARGET_AHEAD = "target_ahead"
    FEATURE_AHEAD = "feature_ahead"
    DIVERGED = "diverged"
    UNKNOWN = "unknown"

    @property
    def needs_catch_up(self) -> bool:
        """The feature lacks target commits (unproven counts as lacking)."""
        return self in {
            BranchRelation.TARGET_AHEAD,
            BranchRelation.DIVERGED,
            BranchRelation.UNKNOWN,
        }

    @property
    def needs_landing(self) -> bool:
        """The target lacks feature commits (unproven counts as lacking)."""
        return self in {
            BranchRelation.FEATURE_AHEAD,
            BranchRelation.DIVERGED,
            BranchRelation.UNKNOWN,
        }


def _operation_in_flight(root: Path) -> bool:
    """Whether a rebase or merge is paused here; unreadable counts as paused."""
    try:
        return rebase_module.rebase_in_progress(root) or merge_module.merge_state(root) != (
            merge_module.MERGE_STATE_NONE
        )
    except Exception as exc:
        logger.warning("operation-in-flight probe for {} failed: {}", root, exc)
        return True


def branch_relation(repo_root: Path | str, target: str, feature: str = "HEAD") -> BranchRelation:
    """Classify ``feature`` against ``target`` from git's ancestry answers.

    Any unanswered query makes the whole relation ``UNKNOWN``: an
    unreadable repository is never "in sync". So does a rebase or merge in
    flight: a paused rebase detaches ``HEAD`` onto target-side commits, so
    ancestry would read "synced" for a tree that is half replayed.
    """
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
