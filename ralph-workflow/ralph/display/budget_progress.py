from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class BudgetProgress:
    completed: int
    cap: int
    description: str
    tracks_budget: bool
