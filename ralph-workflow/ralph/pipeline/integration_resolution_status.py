from __future__ import annotations

from enum import StrEnum


class IntegrationResolutionStatus(StrEnum):
    RESOLVED = "resolved"
    RECOVERABLE = "recoverable"
    EXHAUSTED = "exhausted"

    def to_display_label(self) -> str:
        if self is IntegrationResolutionStatus.RESOLVED:
            return "landing"
        if self is IntegrationResolutionStatus.RECOVERABLE:
            return "waiting"
        return "intervention"


__all__ = ["IntegrationResolutionStatus"]
