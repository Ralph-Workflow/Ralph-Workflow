"""PhaseTimingRecord: structured timing result for a completed phase execution."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta


@dataclass(frozen=True)
class PhaseTimingRecord:
    """Structured timing result for a completed phase execution."""

    phase: str
    iteration: int
    started_at: float
    elapsed: timedelta
    elapsed_seconds: int
