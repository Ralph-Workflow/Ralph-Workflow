
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class PostCommitRouteExplanation:

    phase: str
    budget_state: str
    target: str
