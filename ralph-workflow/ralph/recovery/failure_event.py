"""Structured failure event emitted for every classified failure."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime


@dataclass(frozen=True)
class FailureEvent:
    """Structured failure event emitted for every classified failure."""

    timestamp: datetime
    phase: str
    agent: str | None
    category: str
    reason: str
    counted_against_budget: bool
    chain_capacity_remaining: int
    recovery_cycle: int
    retry_delay_ms: int = 0
    watchdog_reason: str | None = None
    unavailability_reason: str | None = None
