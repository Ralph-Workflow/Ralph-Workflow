from __future__ import annotations

from dataclasses import dataclass

__all__ = ["NextStrategyOutcome"]


@dataclass(frozen=True, slots=True)
class NextStrategyOutcome:
    strategy: str
