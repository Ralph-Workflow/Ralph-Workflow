from __future__ import annotations

from enum import StrEnum

__all__ = ("LadderState",)


class LadderState(StrEnum):
    NORMAL_CLEANUP = "normal_cleanup"
    ALTERNATE_PRESERVATION = "alternate_preservation"
    QUARANTINE = "quarantine"
    FRESH_WORKTREE = "fresh_worktree"
    ENVIRONMENT_DEGRADE = "environment_degrade"
    LAST_RESORT = "last_resort"
    LANDED = "landed"
    NEEDS_OPERATOR = "NeedsOperator"
