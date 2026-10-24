"""Integration adversity catalogue fault kinds."""

from __future__ import annotations

from enum import Enum, auto


class FaultKind(Enum):
    """Named failure modes from the integration adversity catalogue."""

    NONE = auto()
    GIT_TIMEOUT = auto()
    LOCK_CONTENTION = auto()
    CRASH_BEFORE_STEP = auto()
    CRASH_AFTER_STEP = auto()
    REMOTE_OUTAGE = auto()
    CONCURRENT_TARGET_ADVANCE = auto()
    REAL_CONFLICT = auto()
    STALE_LOCK = auto()
