"""Explanation of a phase's verification policy."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class VerificationExplanation:
    """Explanation of a phase's verification policy."""

    kind: str
    gate_for: str
    on_failure_route: str | None
