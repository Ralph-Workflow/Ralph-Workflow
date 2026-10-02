"""Data types and states for the explore recovery scheduler."""

from __future__ import annotations

import enum
from dataclasses import dataclass


class HealthState(enum.StrEnum):
    """Health states reported by the scheduler."""

    HEALTHY = "healthy"
    BUILDING = "building"
    STALE = "stale"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"


@dataclass
class _RecoveryState:
    """Snapshot of the scheduler state for the status payload."""

    health: HealthState = HealthState.HEALTHY
    last_failure_code: str | None = None
    last_failure_message: str | None = None
    recovery_attempts: int = 0
    next_recovery_at: float | None = None
    cooldown_remaining: float = 0.0


__all__ = [
    "HealthState",
    "_RecoveryState",
]
