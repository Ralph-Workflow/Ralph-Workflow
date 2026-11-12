from __future__ import annotations

from enum import StrEnum


class TimeoutProfile(StrEnum):
    STANDARD = "standard"
    ACTIVITY_ONLY = "activity_only"


__all__ = ["TimeoutProfile"]
