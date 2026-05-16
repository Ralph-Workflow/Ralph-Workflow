"""Immutable budget state for a single (phase, agent) pair."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.recovery.classifier import ClassifiedFailure


@dataclass(frozen=True)
class BudgetState:
    """Immutable budget state for a single (phase, agent) pair."""

    max_retries: int
    consumed: int = 0
    failures: tuple[ClassifiedFailure, ...] = field(default_factory=tuple)

    @property
    def exhausted(self) -> bool:
        return self.consumed >= self.max_retries

    @property
    def remaining(self) -> int:
        return max(0, self.max_retries - self.consumed)
