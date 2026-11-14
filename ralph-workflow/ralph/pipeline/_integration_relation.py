from __future__ import annotations

from enum import StrEnum

__all__ = ["IntegrationRelation"]


class IntegrationRelation(StrEnum):
    EQUAL = "equal"
    AHEAD = "ahead"
    BEHIND = "behind"
    DIVERGED = "diverged"
    UNRELATED = "unrelated"
