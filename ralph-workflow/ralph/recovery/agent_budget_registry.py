"""Registry mapping (phase, agent_name) to budget state."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .budget_state import BudgetState

if TYPE_CHECKING:
    from collections.abc import Iterable

    from .classifier import ClassifiedFailure


class AgentBudgetRegistry:
    """Registry mapping (phase, agent_name) -> BudgetState.

    Immutable-value-returning: debit/reset return new registry instances.
    """

    def __init__(self, budgets: dict[tuple[str, str], BudgetState] | None = None) -> None:
        self._budgets: dict[tuple[str, str], BudgetState] = budgets or {}

    def get(self, phase: str, agent: str) -> BudgetState | None:
        return self._budgets.get((phase, agent))

    def set_budget(self, phase: str, agent: str, max_retries: int) -> AgentBudgetRegistry:
        """Return a new registry with this budget initialized."""
        new = dict(self._budgets)
        new[(phase, agent)] = BudgetState(max_retries=max_retries)
        return AgentBudgetRegistry(new)

    def debit(self, phase: str, agent: str, failure: ClassifiedFailure) -> AgentBudgetRegistry:
        """Return a new registry with the failure debited for (phase, agent)."""
        if not failure.counts_against_budget:
            return self
        current = self._budgets.get((phase, agent), BudgetState(max_retries=3))
        new_state = BudgetState(
            max_retries=current.max_retries,
            consumed=current.consumed + 1,
            failures=(*current.failures, failure),
        )
        new = dict(self._budgets)
        new[(phase, agent)] = new_state
        return AgentBudgetRegistry(new)

    def reset(self, phase: str, agent: str) -> AgentBudgetRegistry:
        """Return a new registry with the budget for (phase, agent) reset."""
        current = self._budgets.get((phase, agent))
        if current is None:
            return self
        new = dict(self._budgets)
        new[(phase, agent)] = BudgetState(max_retries=current.max_retries)
        return AgentBudgetRegistry(new)

    def is_exhausted(self, phase: str, agent: str) -> bool:
        """Check if the budget for (phase, agent) is exhausted."""
        state = self._budgets.get((phase, agent))
        if state is None:
            return False
        return state.exhausted

    def items(self) -> Iterable[tuple[tuple[str, str], BudgetState]]:
        """Iterate over ((phase, agent), state) pairs without exposing the internal dict."""
        return self._budgets.items()
