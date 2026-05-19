"""Policy-driven budget registry seeding."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .agent_budget_registry import AgentBudgetRegistry

if TYPE_CHECKING:
    from ralph.policy.models import PolicyBundle


def seed_budget_registry(bundle: PolicyBundle) -> AgentBudgetRegistry:
    """Seed the budget registry from policy bundle configuration."""
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
                registry = registry.set_budget(phase_name, agent_name, max_retries)

    return registry
