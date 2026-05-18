"""Structured failure event emitted for every classified failure."""

from __future__ import annotations

from dataclasses import dataclass
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
