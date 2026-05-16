"""Explanation of a budget counter."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class BudgetCounterExplanation:
    """Explanation of a budget counter."""

    name: str
    description: str
    tracks_budget: bool
    default_max: int = 0
