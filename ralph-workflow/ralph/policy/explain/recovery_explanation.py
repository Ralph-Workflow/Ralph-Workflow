"""Explanation of the recovery policy."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class RecoveryExplanation:
    """Explanation of the recovery policy."""

    cycle_cap: int
    terminal_recovery_route: str
    preserve_session_on_categories: list[str]
