from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class NextStrategyOutcome:
    strategy: str


__all__ = ["NextStrategyOutcome"]
