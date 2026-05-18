"""Immutable progress record for a policy-declared budget counter."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class BudgetProgress:
    """Immutable progress record for a single policy-declared budget counter."""

    completed: int
    cap: int
    description: str
    tracks_budget: bool
