from __future__ import annotations

from enum import StrEnum


class SessionDrain(StrEnum):
    PLANNING = "planning"
    DEVELOPMENT = "development"
    DEVELOPMENT_ANALYSIS = "development_analysis"
    DEVELOPMENT_COMMIT = "development_commit"
    ANALYSIS = "analysis"
    REVIEW = "review"
    REVIEW_ANALYSIS = "review_analysis"
    REVIEW_COMMIT = "review_commit"
    FIX = "fix"
    COMMIT = "commit"


__all__ = ["SessionDrain"]
