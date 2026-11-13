from __future__ import annotations

from enum import StrEnum

__all__ = ("WaitKind",)


class WaitKind(StrEnum):
    AVAILABLE_NOW = "available_now"
    COOLING_DOWN = "cooling_down"
    NEVER_ELIGIBLE = "never_eligible"
