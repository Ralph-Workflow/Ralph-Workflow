from __future__ import annotations

from enum import StrEnum


class EvidenceVerdict(StrEnum):
    UNCHANGED = "unchanged"
    CHANGED = "changed"
    UNKNOWN = "unknown"


__all__ = ["EvidenceVerdict"]
