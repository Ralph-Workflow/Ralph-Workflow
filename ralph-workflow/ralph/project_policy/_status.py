from __future__ import annotations

from enum import StrEnum


class ReadinessStatus(StrEnum):
    READY = "ready"
    SKIPPED = "skipped"
    REMEDIATION_REQUIRED = "remediation-required"
    BLOCKED = "blocked"


__all__ = ["ReadinessStatus"]
