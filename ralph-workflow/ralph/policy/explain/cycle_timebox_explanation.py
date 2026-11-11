
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class CycleTimeboxExplanation:

    duration_seconds: float
    warning_threshold_seconds: float
    start_source: str
    start_entry: str
    guarded_entry: str
    end_entry: str
    finalization_target: str
    finalization_cycle_outcome: str
