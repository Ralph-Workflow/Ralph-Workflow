from __future__ import annotations

from enum import StrEnum

__all__ = ["IntegrationStrategy"]


class IntegrationStrategy(StrEnum):
    NOTHING_TO_DO = "nothing_to_do"
    FAST_FORWARD = "fast_forward"
    REBASE = "rebase"
    MERGE = "merge"
    COMPLETE_IN_PLACE = "complete_in_place"
