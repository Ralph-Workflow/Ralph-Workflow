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
        """Return a new budget with the failure counted (only if it counts)."""
        if not failure.counts_against_budget:
            return self
        new_state = BudgetState(
            max_retries=self.state.max_retries,
            consumed=self.state.consumed + 1,
            failures=(*self.state.failures, failure),
        )
        return FailureBudget(state=new_state)

    def reset(self) -> FailureBudget:
        """Return a fresh budget with the same max_retries."""
        return FailureBudget(state=BudgetState(max_retries=self.state.max_retries))

    @property
    def exhausted(self) -> bool:
        return self.state.exhausted

    @property
    def remaining(self) -> int:
        return self.state.remaining
