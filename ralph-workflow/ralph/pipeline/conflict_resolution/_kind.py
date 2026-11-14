from __future__ import annotations

from enum import StrEnum

__all__ = ["ConflictKind"]


class ConflictKind(StrEnum):
    TEXT = "Text"
    GITLINK = "Gitlink"
    DECLARED_DECISION = "DeclaredDecision"
    DELETE_MODIFY = "DeleteModify"
    BINARY = "Binary"
    APPEND_ONLY = "AppendOnly"
    OTHER = "Other"
