"""DrainClass — drain class used for capability defaults."""

from __future__ import annotations

from enum import StrEnum


class DrainClass(StrEnum):
    """Drain class used for capability defaults."""

    PLANNING = "planning"
    DEVELOPMENT = "development"
    ANALYSIS = "analysis"
    REVIEW = "review"
    FIX = "fix"
    COMMIT = "commit"

    def allows_write(self) -> bool:
        """Return whether this drain class allows write operations."""
        return self in {DrainClass.DEVELOPMENT, DrainClass.FIX}


__all__ = ["DrainClass"]
