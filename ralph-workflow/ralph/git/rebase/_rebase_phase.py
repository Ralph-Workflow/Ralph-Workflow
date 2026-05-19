"""RebasePhase — lifecycle phase enum for an in-progress rebase operation."""

from __future__ import annotations

from enum import StrEnum


class RebasePhase(StrEnum):
    """Lifecycle phase of an in-progress rebase operation."""

    NotStarted = "not_started"
    PreRebaseCheck = "pre_rebase_check"
    RebaseInProgress = "rebase_in_progress"
    ConflictDetected = "conflict_detected"
    ConflictResolutionInProgress = "conflict_resolution_in_progress"
    CompletingRebase = "completing_rebase"
    RebaseComplete = "rebase_complete"
    RebaseAborted = "rebase_aborted"

    def max_recovery_attempts(self) -> int:
        if self == RebasePhase.ConflictResolutionInProgress:
            return 5
        if self in {RebasePhase.RebaseInProgress, RebasePhase.CompletingRebase}:
            return 2
        if self == RebasePhase.PreRebaseCheck:
            return 1
        return 3


__all__ = ["RebasePhase"]
