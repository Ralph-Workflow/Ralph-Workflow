"""Failure budget tracking per agent in the pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.policy.models import PolicyBundle
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
        return FailureBudget(
            state=BudgetState(max_retries=self.state.max_retries)
        )

    @property
    def exhausted(self) -> bool:
        return self.state.exhausted

    @property
    def remaining(self) -> int:
        return self.state.remaining


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


def seed_budget_registry(bundle: PolicyBundle) -> AgentBudgetRegistry:
    """Seed the budget registry from policy bundle configuration.

    This is the single place where budgets are initialized from policy.
    It iterates over all chains and all phases bound to those chains,
    using each chain's max_retries as the budget.

    Args:
        bundle: The loaded policy bundle.

    Returns:
        AgentBudgetRegistry seeded with all chain budgets.
    """
    registry = AgentBudgetRegistry()

    # Map phases to their drain names for lookup
    phase_to_drain: dict[str, str] = {}
    for phase_name, phase_def in bundle.pipeline.phases.items():
        phase_to_drain[phase_name] = phase_def.drain

    for chain_name, chain_config in bundle.agents.agent_chains.items():
        max_retries = chain_config.max_retries
        for phase_name, drain_name in phase_to_drain.items():
            drain_config = bundle.agents.agent_drains.get(drain_name)
            if drain_config is None:
                continue
            if drain_config.chain != chain_name:
                continue
            # This phase uses this chain - seed budgets for each agent in the chain
            for agent_name in chain_config.agents:
                registry = registry.set_budget(phase_name, agent_name, max_retries)

    return registry
