
from __future__ import annotations

from enum import StrEnum


class DrainClass(StrEnum):

    PLANNING = "planning"
    DEVELOPMENT = "development"
    ANALYSIS = "analysis"
    REVIEW = "review"
    FIX = "fix"
    COMMIT = "commit"

    def allows_write(self) -> bool:
        return self in {DrainClass.DEVELOPMENT, DrainClass.FIX}


__all__ = ["DrainClass"]
