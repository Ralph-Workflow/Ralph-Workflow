
from __future__ import annotations

from enum import StrEnum


class FailureCategory(StrEnum):

    INTEGRATION = "integration"
    ENVIRONMENTAL = "environmental"
    AGENT = "agent"
    USER_CONFIG = "user_config"
    ARTIFACT_VALIDATION = "artifact_validation"
    AMBIGUOUS = "ambiguous"
