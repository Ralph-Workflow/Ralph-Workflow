"""Per-agent failure budget wrapper."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .budget_state import BudgetState

if TYPE_CHECKING:
    from .classifier import ClassifiedFailure


@dataclass(frozen=True)
class FailureBudget:
    """Per-agent failure budget wrapper."""

    state: BudgetState

    def debit(self, failure: ClassifiedFailure) -> FailureBudget:
        """Return a new budget with the failure counted (only if it counts).

        The previous ``failures=(*self.state.failures, failure)`` accumulator
        was removed in wt-024 memory-perf AC-01: the failures tuple was
        appended on every debit and never read for any decision, while
        retaining heavyweight ``ClassifiedFailure`` objects
        (original_exception + traceback frames) for the lifetime of the
        budget. Only ``consumed`` is needed to drive the
        exhausted / remaining decisions.
        """
        if not failure.counts_against_budget:
            return self
        new_state = BudgetState(
            max_retries=self.state.max_retries,
            consumed=self.state.consumed + 1,
        )
        return FailureBudget(state=new_state)

    @property
    def exhausted(self) -> bool:
        return self.state.exhausted

    @property
    def remaining(self) -> int:
        return self.state.remaining
