
from __future__ import annotations

from enum import StrEnum

__all__ = ["ConflictAttemptOutcome"]


class ConflictAttemptOutcome(StrEnum):

    UNRESOLVED_REMAINING = "unresolved_remaining"
    NO_ACTIVITY = "no_activity"
    ATTEMPT_FAILED = "attempt_failed"
    RAW = "raw"
