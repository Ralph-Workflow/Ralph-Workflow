
from __future__ import annotations

from enum import StrEnum


class PolicyOutcomeStatus(StrEnum):

    APPROVED = "approved"
    DENIED = "denied"
    APPROVED_WITH_RESTRICTION = "approved_with_restriction"


__all__ = ["PolicyOutcomeStatus"]
