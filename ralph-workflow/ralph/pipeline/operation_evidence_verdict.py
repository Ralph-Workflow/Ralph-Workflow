"""Closed verdict vocabulary for operation-scoped diagnostic evidence."""

from __future__ import annotations

from enum import StrEnum


class EvidenceVerdict(StrEnum):
    """Closed vocabulary for operation-scoped diagnostic evidence evaluation."""

    UNCHANGED = "unchanged"
    CHANGED = "changed"
    UNKNOWN = "unknown"


__all__ = ["EvidenceVerdict"]
