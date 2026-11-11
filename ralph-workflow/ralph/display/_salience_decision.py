
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.display._frequency_tier import FrequencyTier


@dataclass(frozen=True)
class AllocationDecision:

    role: str
    tier: FrequencyTier
    lit: bool
    reason: str
    frame_index: int


__all__ = ["AllocationDecision"]
