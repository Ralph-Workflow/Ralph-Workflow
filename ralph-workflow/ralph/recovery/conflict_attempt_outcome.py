from __future__ import annotations

from enum import StrEnum

__all__ = ["ConflictAttemptOutcome", "attempt_outcome_reason"]


class ConflictAttemptOutcome(StrEnum):
    UNRESOLVED_REMAINING = "unresolved_remaining"
    STALLED = "stalled"
    STALLED_NO_PROGRESS = "stalled_no_progress"
    NO_ACTIVITY = "no_activity"
    ATTEMPT_FAILED = "attempt_failed"
    RAW = "raw"


def attempt_outcome_reason(
    outcome: ConflictAttemptOutcome,
    unresolved_count: int = 0,
) -> str:
    if outcome in (
        ConflictAttemptOutcome.UNRESOLVED_REMAINING,
        ConflictAttemptOutcome.STALLED,
        ConflictAttemptOutcome.STALLED_NO_PROGRESS,
    ):
        return f"agent reported success but {unresolved_count} conflicted path(s) remain"
    if outcome is ConflictAttemptOutcome.NO_ACTIVITY:
        return "candidate produced no activity before it exited"
    return "conflict attempt failed"

