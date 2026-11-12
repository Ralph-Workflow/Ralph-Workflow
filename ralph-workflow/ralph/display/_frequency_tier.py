from __future__ import annotations

from enum import IntEnum


class FrequencyTier(IntEnum):
    FIELD = 1
    STRUCTURE = 2
    EVENT = 3
    ALARM = 4


__all__ = ["FrequencyTier"]
