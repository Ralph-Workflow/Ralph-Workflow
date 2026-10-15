"""Registry mapping (phase, agent_name) to budget state."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING

from .budget_state import BudgetState

if TYPE_CHECKING:
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

    def set_budget(
        self, phase: str, agent: str, max_retries: int, consumed: int = 0
    ) -> AgentBudgetRegistry:
        """Return a new registry with this budget initialized."""
        new = dict(self._budgets)
        new[(phase, agent)] = BudgetState(max_retries=max_retries, consumed=consumed)
        return AgentBudgetRegistry(new)

    def to_dict(self) -> dict[str, dict[str, int]]:
        """Return serializable representation of registry state."""
        return {
            f"{phase}:{agent}": state.to_dict() for (phase, agent), state in self._budgets.items()
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> AgentBudgetRegistry:
        """Construct an AgentBudgetRegistry from a serialized mapping."""
        budgets: dict[tuple[str, str], BudgetState] = {}
        for key, value in data.items():
            if isinstance(value, BudgetState):
                state = value
            elif isinstance(value, Mapping):
                state = BudgetState.from_dict(value)
            else:
                continue
            if ":" in key:
                parts = key.split(":", 1)
                budgets[(parts[0], parts[1])] = state
        return cls(budgets)

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

    def replenish_phase(self, phase: str) -> AgentBudgetRegistry:
        """Return a new registry with every budget for ``phase`` reset to zero.

        Each recovery cycle re-enters the phase with a fresh chain
        (``reset_phase_chain_for_recovery``), so the per-attempt budgets must
        be replenished too; otherwise ``preferred_agent_index`` treats every
        agent as spent after the first cycle and the run exits prematurely.
        Cross-restart durability is preserved separately: the controller
        snapshot serializes ``consumed`` before the cycle boundary and
        ``seed_budget_registry`` restores it when rebuilding the registry.
        """
        new = {
            key: (
                BudgetState(max_retries=state.max_retries, consumed=0) if key[0] == phase else state
            )
            for key, state in self._budgets.items()
        }
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
