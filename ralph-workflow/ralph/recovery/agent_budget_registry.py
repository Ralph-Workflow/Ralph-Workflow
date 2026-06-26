"""Registry mapping (phase, agent_name) to budget state."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .budget_state import BudgetState

if TYPE_CHECKING:
    from collections.abc import Iterable

    from .classifier import ClassifiedFailure


class AgentBudgetRegistry:
    """Registry mapping (phase, agent_name) -> BudgetState.

    Immutable-value-returning: ``debit`` returns a new registry instance.
    The previous ``reset`` method was removed in wt-024 memory-perf
    AC-01: it had zero callers (repo-wide grep) and violated the
    AGENTS.md "Absolutely Zero Dead code" rule.
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
        """Return a new registry with the failure debited for (phase, agent).

        The previous ``failures=(*current.failures, failure)`` accumulator
        was removed in wt-024 memory-perf AC-01: the failures tuple was
        appended on every debit and never read for any decision, while
        retaining heavyweight ``ClassifiedFailure`` objects
        (original_exception + traceback frames) for the lifetime of the
        registry. Only ``consumed`` is needed to drive the
        exhausted / remaining decisions.
        """
        if not failure.counts_against_budget:
            return self
        current = self._budgets.get((phase, agent), BudgetState(max_retries=3))
        new_state = BudgetState(
            max_retries=current.max_retries,
            consumed=current.consumed + 1,
        )
        new = dict(self._budgets)
        new[(phase, agent)] = new_state
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
