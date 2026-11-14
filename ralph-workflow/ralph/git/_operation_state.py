from __future__ import annotations

from enum import StrEnum

__all__ = ["OperationState"]


class OperationState(StrEnum):
    NONE = "NONE"
    REBASE_PAUSED = "REBASE_PAUSED"
    MERGE_PAUSED = "MERGE_PAUSED"
    DETACHED = "DETACHED"
    UNKNOWN = "UNKNOWN"
