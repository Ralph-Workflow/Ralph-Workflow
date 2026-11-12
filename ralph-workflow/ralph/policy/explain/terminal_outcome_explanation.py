from __future__ import annotations

from dataclasses import dataclass


@dataclass
class TerminalOutcomeExplanation:
    phase: str
    outcome: str
