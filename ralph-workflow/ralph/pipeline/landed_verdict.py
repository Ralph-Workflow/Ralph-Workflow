from __future__ import annotations

from enum import StrEnum

__all__ = ("LandedVerdict",)


class LandedVerdict(StrEnum):
    LANDED = "landed"
    NOT_LANDED = "not_landed"
    UNKNOWN = "unknown"
