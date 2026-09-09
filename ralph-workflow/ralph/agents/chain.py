"""Agent fallback chain management with strict drain-to-chain binding.

This module handles the agent fallback chain — the ordered list of agents
to try when an agent fails. It supports retry logic and exponential backoff.

IMPORTANT: This module implements STRICT drain-to-chain binding. Every drain
must have an explicit binding in AgentsPolicy or startup validation fails.
There is NO permissive fallback resolution — no sibling fallback, no inference,
no default chains. If a drain is not bound, DrainNotBoundError is raised.
"""

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
    """Manages agent chains with strict drain-to-chain binding.

    ChainManager is constructed with an AgentsPolicy and provides lookup of
    chains by drain name. Drain resolution is STRICT — there is no fallback
    or inference. If a drain is not explicitly bound, DrainNotBoundError
    is raised.

    Attributes:
        agents_policy: The agents policy containing chains and drain bindings.
    """

    def __init__(self, agents_policy: AgentsPolicy) -> None:
        """Initialize ChainManager with an AgentsPolicy.

        Args:
            agents_policy: Validated agents policy with chain and drain definitions.
        """
        self._policy = agents_policy

    def chain_for_drain(self, drain: DrainName) -> AgentChainConfig:
        """Get the chain configuration for a drain.

        This is the STRICT drain resolution — no fallback, no inference.
        If the drain is not explicitly bound in agents.toml, DrainNotBoundError
        is raised at startup before any agent is invoked.

        Args:
            drain: Drain name to look up.

        Returns:
            AgentChainConfig for the bound chain.

        Raises:
            DrainNotBoundError: If the drain is not explicitly bound.
        """
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
        """Alias for chain_for_drain for clarity."""
        return self.chain_for_drain(drain)

    def validate(self) -> list[str]:
        """Validate the policy for internal consistency.

        Returns:
            List of validation error messages (empty if valid).
        """
        errors: list[str] = []

        for drain, binding in self._policy.agent_drains.items():
            if binding.chain not in self._policy.agent_chains:
                errors.append(f"Drain '{drain}' references unknown chain '{binding.chain}'")

        for name, chain in self._policy.agent_chains.items():
            if not chain.agents:
                errors.append(f"Chain '{name}' has no agents")

        return errors
