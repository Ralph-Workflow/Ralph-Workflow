"""Immutable budget state for a single (phase, agent) pair."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping


@dataclass(frozen=True)
class BudgetState:
    """Immutable budget state for a single (phase, agent) pair.

    ``max_retries`` and ``consumed`` are the only counters needed to
    drive every budget decision (exhausted / remaining). A previous
    ``failures: tuple[ClassifiedFailure, ...]`` accumulator was
    removed in wt-024 memory-perf AC-01: it was appended on every
    debit, never read for any decision, and retained heavyweight
    ``ClassifiedFailure`` objects (original_exception + traceback
    frames) across an entire run. Repo-wide grep confirmed zero
    readers.
    """

    max_retries: int
    consumed: int = 0

    @property
    def exhausted(self) -> bool:
        return self.consumed >= self.max_retries

    @property
    def remaining(self) -> int:
        return max(0, self.max_retries - self.consumed)

    def to_dict(self) -> dict[str, int]:
        return {"max_retries": self.max_retries, "consumed": self.consumed}

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> BudgetState:
        max_retries_raw = data.get("max_retries", 3)
        consumed_raw = data.get("consumed", 0)
        max_retries = int(max_retries_raw) if isinstance(max_retries_raw, (int, str)) else 3
        consumed = int(consumed_raw) if isinstance(consumed_raw, (int, str)) else 0
        return cls(max_retries=max_retries, consumed=consumed)
