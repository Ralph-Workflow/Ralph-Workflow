from __future__ import annotations

from typing import TYPE_CHECKING

from .agent_chain import AgentChain
from .drain_not_bound_error import DrainNotBoundError
from .unknown_agent_error import UnknownAgentError

if TYPE_CHECKING:
    from ralph.policy.models import AgentChainConfig, AgentsPolicy, DrainName

__all__ = [
    "AgentChain",
    "ChainManager",
    "DrainNotBoundError",
    "UnknownAgentError",
]


class ChainManager:
    def __init__(self, agents_policy: AgentsPolicy) -> None:
        self._policy = agents_policy

    def chain_for_drain(self, drain: DrainName) -> AgentChainConfig:
        binding = self._policy.agent_drains.get(drain)
        if binding is None:
            raise DrainNotBoundError(
                drain=drain,
                available_drains=set(self._policy.agent_drains.keys()),
            )

        chain = self._policy.agent_chains.get(binding.chain)
        if chain is None:
            msg = (
                f"Drain '{drain}' references chain '{binding.chain}' "
                f"which is not defined in agent_chains"
            )
            raise ValueError(msg)

        return chain

    def chain_config_for_drain(self, drain: DrainName) -> AgentChainConfig:
        return self.chain_for_drain(drain)

    def validate(self) -> list[str]:
        errors: list[str] = []

        for drain, binding in self._policy.agent_drains.items():
            if binding.chain not in self._policy.agent_chains:
                errors.append(f"Drain '{drain}' references unknown chain '{binding.chain}'")

        for name, chain in self._policy.agent_chains.items():
            if not chain.agents:
                errors.append(f"Chain '{name}' has no agents")

        return errors
