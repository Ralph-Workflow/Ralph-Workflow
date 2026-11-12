from __future__ import annotations

from enum import Enum, auto


class LeaseOutcomeKind(Enum):
    ACQUIRED = auto()
    HELD_BY_LIVE_OWNER = auto()
    UNKNOWN = auto()


__all__ = ["LeaseOutcomeKind"]
