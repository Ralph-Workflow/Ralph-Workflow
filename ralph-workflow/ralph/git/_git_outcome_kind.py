
from __future__ import annotations

from enum import StrEnum

__all__ = ["GitOutcomeKind"]


class GitOutcomeKind(StrEnum):

    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    KILLED = "killed"
    UNKNOWN = "unknown"
