from __future__ import annotations

from typing import TYPE_CHECKING, Final

from ralph.pipeline.conflict_resolution.graph import PHASE_RESOLUTION
from ralph.pipeline.last_resort_agent_pool import LastResortAgentPool

if TYPE_CHECKING:
    from collections.abc import Sequence

    from ralph.policy.models import PolicyBundle
    from ralph.recovery.controller import RecoveryController

__all__ = [
    "LAST_RESORT_INTEGRATION_PHASE",
    "LAST_RESORT_RESOLVER_PHASE",
    "build_last_resort_agent_pools",
    "production_last_resort_agent_pools",
]

LAST_RESORT_RESOLVER_PHASE: Final[str] = "resolver"
LAST_RESORT_INTEGRATION_PHASE: Final[str] = "integration"


def build_last_resort_agent_pools(
    *,
    resolver_agents: Sequence[str],
    integration_agents: Sequence[str],
) -> tuple[LastResortAgentPool, ...]:
    pools: list[LastResortAgentPool] = []
    resolver = tuple(resolver_agents)
    integration = tuple(integration_agents)
    if resolver:
        pools.append(LastResortAgentPool(LAST_RESORT_RESOLVER_PHASE, resolver))
    if integration:
        pools.append(LastResortAgentPool(LAST_RESORT_INTEGRATION_PHASE, integration))
    return tuple(pools)


def _chain_agents_for_drain(policy_bundle: PolicyBundle, drain_name: str) -> tuple[str, ...]:
    if not hasattr(policy_bundle, "agents"):
        return ()
    agents_policy = policy_bundle.agents
    if not hasattr(agents_policy, "agent_drains") or not hasattr(agents_policy, "agent_chains"):
        return ()
    drain_binding = agents_policy.agent_drains.get(drain_name)
    if drain_binding is None:
        return ()
    chain_config = agents_policy.agent_chains.get(drain_binding.chain)
    if chain_config is None:
        return ()
    return tuple(chain_config.agents)


def production_last_resort_agent_pools(
    *,
    resolver_agents: Sequence[str] | None = None,
    integration_agents: Sequence[str] | None = None,
    controller: RecoveryController | None = None,
    policy_bundle: PolicyBundle | None = None,
    explicit: tuple[LastResortAgentPool, ...] | None = None,
) -> tuple[LastResortAgentPool, ...]:
    if explicit is not None:
        return explicit
    resolved_resolver = tuple(resolver_agents) if resolver_agents is not None else ()
    resolved_integration = tuple(integration_agents) if integration_agents is not None else ()
    if controller is not None:
        if not resolved_resolver:
            resolved_resolver = controller.agents_for_phase(
                LAST_RESORT_RESOLVER_PHASE
            ) or controller.agents_for_phase(PHASE_RESOLUTION)
        if not resolved_integration:
            resolved_integration = controller.agents_for_phase(LAST_RESORT_INTEGRATION_PHASE)
    if policy_bundle is not None:
        if not resolved_resolver:
            from ralph.pipeline.conflict_resolution.session import resolution_chain_agents

            resolved_resolver = resolution_chain_agents(policy_bundle)
        if not resolved_integration:
            resolved_integration = _chain_agents_for_drain(policy_bundle, "development")
    return build_last_resort_agent_pools(
        resolver_agents=resolved_resolver,
        integration_agents=resolved_integration,
    )
