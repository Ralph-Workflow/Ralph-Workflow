from __future__ import annotations

import enum
from dataclasses import dataclass


class HealthState(enum.StrEnum):
    HEALTHY = "healthy"
    BUILDING = "building"
    STALE = "stale"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"


@dataclass
class _RecoveryState:
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
