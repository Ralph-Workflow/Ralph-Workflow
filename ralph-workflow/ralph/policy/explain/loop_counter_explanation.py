
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class LoopCounterExplanation:

    name: str
    default_max: int
    description: str
