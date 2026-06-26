"""Immutable budget state for a single (phase, agent) pair."""

from __future__ import annotations

from dataclasses import dataclass


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
