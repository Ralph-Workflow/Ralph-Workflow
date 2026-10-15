"""Policy-driven budget registry seeding."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING

from .agent_budget_registry import AgentBudgetRegistry
from .budget_state import BudgetState

if TYPE_CHECKING:
    from ralph.policy.models import PolicyBundle


def _prior_consumed(
    prior: AgentBudgetRegistry | Mapping[str, object] | None,
    phase: str,
    agent: str,
) -> int:
    if prior is None:
        return 0
    if isinstance(prior, AgentBudgetRegistry):
        state = prior.get(phase, agent)
        return state.consumed if state is not None else 0
    if isinstance(prior, Mapping):
        entry = prior.get(f"{phase}:{agent}")
        if isinstance(entry, Mapping):
            raw_c = entry.get("consumed", 0)
            return int(raw_c) if isinstance(raw_c, (int, str)) else 0
        if isinstance(entry, BudgetState):
            return entry.consumed
    return 0


def seed_budget_registry(
    bundle: PolicyBundle,
    prior: AgentBudgetRegistry | Mapping[str, object] | None = None,
) -> AgentBudgetRegistry:
    """Seed the budget registry from policy bundle configuration.

    When ``prior`` is provided, previously consumed attempts are preserved
    across restarts so restarts never replenish exhausted budgets.
    """
    registry = AgentBudgetRegistry()

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
            for agent_name in chain_config.agents:
                consumed = _prior_consumed(prior, phase_name, agent_name)
                registry = registry.set_budget(
                    phase_name, agent_name, max_retries, consumed=consumed
                )

    for drain_name, drain_config in bundle.agents.agent_drains.items():
        drain_chain = bundle.agents.agent_chains.get(drain_config.chain)
        if drain_chain is None:
            continue
        for agent_name in drain_chain.agents:
            consumed = _prior_consumed(prior, drain_name, agent_name)
            registry = registry.set_budget(
                drain_name, agent_name, drain_chain.max_retries, consumed=consumed
            )

    return registry
