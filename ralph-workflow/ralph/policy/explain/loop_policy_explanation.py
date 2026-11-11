
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class LoopPolicyExplanation:

    max_iterations: int
    iteration_state_field: str
    loopback_review_outcome: str | None
