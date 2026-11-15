from __future__ import annotations

from enum import StrEnum

__all__ = ["ConflictAttemptOutcome"]


class ConflictAttemptOutcome(StrEnum):
    UNRESOLVED_REMAINING = "unresolved_remaining"
    STALLED = "stalled"
    STALLED_NO_PROGRESS = "stalled_no_progress"
    NO_ACTIVITY = "no_activity"
    ATTEMPT_FAILED = "attempt_failed"
    RAW = "raw"
