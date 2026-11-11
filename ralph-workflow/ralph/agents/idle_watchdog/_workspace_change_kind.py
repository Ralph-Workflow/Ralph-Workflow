
from __future__ import annotations

from enum import StrEnum


class WorkspaceChangeKind(StrEnum):

    SOURCE = "source"
    LOG = "log"
    CACHE = "cache"
    ARTIFACT = "artifact"
    OTHER = "other"


DEFAULT_AGENT_WORKSPACE_CHANGE_WEIGHTS: dict[str, float] = {
    "source": 1.0,
    "log": 0.0,
    "cache": 0.0,
    "artifact": 0.0,
    "other": 0.0,
}


__all__ = [
    "DEFAULT_AGENT_WORKSPACE_CHANGE_WEIGHTS",
    "WorkspaceChangeKind",
]
