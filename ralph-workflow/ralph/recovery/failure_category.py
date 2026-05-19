"""Categories of pipeline failures for attribution and routing."""

from __future__ import annotations

from enum import StrEnum


class FailureCategory(StrEnum):
    """Categories of pipeline failures for attribution and routing."""

    ENVIRONMENTAL = "environmental"
    AGENT = "agent"
    USER_CONFIG = "user_config"
    ARTIFACT_VALIDATION = "artifact_validation"
    AMBIGUOUS = "ambiguous"
