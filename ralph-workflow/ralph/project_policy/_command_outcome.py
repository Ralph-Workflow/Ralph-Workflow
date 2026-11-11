
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CommandOutcome:

    command: str
    exit_code: int
    duration_seconds: float
    policy_content_hash: str


__all__ = ["CommandOutcome"]
