from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import timedelta


@dataclass(frozen=True)
class PhaseTimingRecord:
    phase: str
    iteration: int
    started_at: float
    elapsed: timedelta
    elapsed_seconds: int
