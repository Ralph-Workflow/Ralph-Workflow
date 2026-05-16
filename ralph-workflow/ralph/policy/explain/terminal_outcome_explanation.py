"""Explanation of a terminal phase outcome."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class TerminalOutcomeExplanation:
    """Explanation of a terminal phase outcome."""

    phase: str
    outcome: str
