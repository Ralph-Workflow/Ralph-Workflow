
from __future__ import annotations

from enum import StrEnum

from ralph.mcp.protocol._access_mode import AccessMode


class PolicyMode(StrEnum):

    PLANNING = "planning"
    DEVELOPMENT = "development"
    ANALYSIS = "analysis"
    REVIEW = "review"
    FIX = "fix"
    COMMIT = "commit"

    def access_mode(self) -> AccessMode:
        if self in {PolicyMode.DEVELOPMENT, PolicyMode.FIX}:
            return AccessMode.READ_WRITE
        return AccessMode.READ_ONLY


__all__ = ["PolicyMode"]
