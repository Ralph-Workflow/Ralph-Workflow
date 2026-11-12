from __future__ import annotations

from dataclasses import dataclass


@dataclass
class VerificationExplanation:
    kind: str
    gate_for: str
    on_failure_route: str | None
