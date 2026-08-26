"""Status vocabulary for the integration-resolution dispatch invariant."""

from __future__ import annotations

from enum import StrEnum


class IntegrationResolutionStatus(StrEnum):
    """Closed dispatch verdict vocabulary shared by all integration boundaries."""

    RESOLVED = "resolved"
    RECOVERABLE = "recoverable"
    EXHAUSTED = "exhausted"
