"""Status vocabulary for the integration-resolution dispatch invariant."""

from __future__ import annotations

from enum import StrEnum


class IntegrationResolutionStatus(StrEnum):
    """Closed dispatch verdict vocabulary shared by all integration boundaries."""

    RESOLVED = "resolved"
    RECOVERABLE = "recoverable"
    EXHAUSTED = "exhausted"

    def to_display_label(self) -> str:
        """Derive the canonical operator display label for this dispatch status."""
        if self is IntegrationResolutionStatus.RESOLVED:
            return "landing"
        if self is IntegrationResolutionStatus.RECOVERABLE:
            return "waiting"
        return "intervention"


__all__ = ["IntegrationResolutionStatus"]
