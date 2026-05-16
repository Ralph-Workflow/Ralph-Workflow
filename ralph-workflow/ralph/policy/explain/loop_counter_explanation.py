"""Explanation of a loop counter."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class LoopCounterExplanation:
    """Explanation of a loop counter."""

    name: str
    default_max: int
    description: str
