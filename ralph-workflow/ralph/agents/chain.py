"""Agent fallback chain management with strict drain-to-chain binding.

This module handles the agent fallback chain — the ordered list of agents
to try when an agent fails. It supports retry logic and exponential backoff.

IMPORTANT: This module implements STRICT drain-to-chain binding. Every drain
must have an explicit binding in AgentsPolicy or startup validation fails.
There is NO permissive fallback resolution — no sibling fallback, no inference,
no default chains. If a drain is not bound, DrainNotBoundError is raised.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from loguru import logger

from ralph.policy.models import AgentChainConfig, AgentDrainConfig, AgentsPolicy, DrainName

if TYPE_CHECKING:
    from ralph.config.models import UnifiedConfig


class DrainNotBoundError(Exception):
    """Raised when a drain has no explicit chain binding.

    Attributes:
        drain: The unbound drain name.
        available_drains: Names of all bound drains.
    """

    def __init__(self, drain: str, available_drains: set[str]) -> None:
        self.drain = drain
        self.available_drains = available_drains
        available = sorted(available_drains)
        msg = (
            f"Drain '{drain}' is not bound to any agent chain in agents.toml. "
            f"Available drains: {available}. "
            f"Add a binding for '{drain}' in agent_drains or use a bound drain."
        )
        super().__init__(msg)


class UnknownAgentError(Exception):
    """Raised when an agent name is not found in the registry.

    Attributes:
        agent_name: The unknown agent name.
    """

    def __init__(self, agent_name: str) -> None:
        self.agent_name = agent_name
        msg = f"Unknown agent: '{agent_name}'. Register the agent in the configuration."
        super().__init__(msg)


class AgentChain:
    """Manages agent fallback chain with retry logic.

    The chain maintains an ordered list of agents and handles:
    - Current agent selection
    - Retry counting and limits
    - Exponential backoff between retries
    - Fallback to next agent on exhaustion

    Attributes:
        agents: List of agent names in the chain.
        current_index: Index of the currently selected agent.
        retries: Number of retries for current agent.
        max_retries: Maximum retries before falling back.
        retry_delay_ms: Base delay between retries in milliseconds.
        backoff_multiplier: Multiplier for exponential backoff.
        max_backoff_ms: Maximum backoff delay in milliseconds.
    """

    def __init__(
        self,
        agents: list[str],
        max_retries: int = 3,
        retry_delay_ms: int = 1000,
        backoff_multiplier: float = 2.0,
        max_backoff_ms: int = 60000,
    ) -> None:
        """Initialize agent chain.

        Args:
            agents: List of agent names in fallback order.
            max_retries: Maximum retries per agent before fallback.
            retry_delay_ms: Base delay between retries in milliseconds.
            backoff_multiplier: Multiplier for exponential backoff.
            max_backoff_ms: Maximum backoff delay in milliseconds.
        """
        self.agents = agents
        self.current_index = 0
        self.retries = 0
        self.max_retries = max_retries
        self.retry_delay_ms = retry_delay_ms
        self.backoff_multiplier = backoff_multiplier
        self.max_backoff_ms = max_backoff_ms

    @property
    def current_agent(self) -> str | None:
        """Get the current agent name.

        Returns:
            Agent name or None if chain is exhausted.
        """
        if not self.agents or self.current_index >= len(self.agents):
            return None
        return self.agents[self.current_index]

    @property
    def is_exhausted(self) -> bool:
        """Check if all agents in chain are exhausted.

        Returns:
            True if no agents remain.
        """
        return self.current_agent is None

    def can_retry(self) -> bool:
        """Check if current agent can be retried.

        Returns:
            True if retries remain for current agent.
        """
        return self.retries < self.max_retries

    def advance(self) -> bool:
        """Advance to the next agent in the chain.

        Returns:
            True if advanced successfully, False if chain exhausted.
        """
        if self.current_index + 1 < len(self.agents):
            self.current_index += 1
            self.retries = 0
            logger.debug("Advanced to next agent: {}", self.current_agent)
            return True
        logger.debug("Agent chain exhausted")
        return False

    def record_retry(self) -> None:
        """Record a retry attempt for current agent."""
        self.retries += 1
        logger.debug(
            "Retry {} of {} for agent {}",
            self.retries,
            self.max_retries,
            self.current_agent,
        )

    def calculate_backoff(self) -> float:
        """Calculate backoff delay in seconds.

        Returns:
            Backoff delay in seconds.
        """
        delay = self.retry_delay_ms * (self.backoff_multiplier**self.retries)
        return min(delay, self.max_backoff_ms) / 1000.0

    def wait_backoff(self) -> None:
        """Wait for the backoff period."""
        backoff = self.calculate_backoff()
        logger.debug("Backing off for {:.2f} seconds", backoff)
        time.sleep(backoff)


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

    @classmethod
    def from_config(cls, config: UnifiedConfig) -> ChainManager:
        """Create ChainManager from a legacy UnifiedConfig.

        This is a compatibility shim that converts the old UnifiedConfig
        format to the new AgentsPolicy format.

        Args:
            config: Legacy unified configuration.

        Returns:
            ChainManager instance.
        """
        agent_chains: dict[str, AgentChainConfig] = {}
        for name, agents in config.agent_chains.items():
            agent_chains[name] = AgentChainConfig(agents=agents)

        agent_drains: dict[DrainName, AgentDrainConfig] = {}
        for drain, chain in config.agent_drains.items():
            agent_drains[drain] = AgentDrainConfig(chain=chain)

        policy = AgentsPolicy(
            agent_chains=agent_chains,
            agent_drains=agent_drains,
        )
        return cls(policy)

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


def create_chain_from_config(
    config: UnifiedConfig,
    chain_name: str,
) -> AgentChain | None:
    """Create an AgentChain from UnifiedConfig.

    Args:
        config: Unified configuration.
        chain_name: Name of the chain in agent_chains.

    Returns:
        AgentChain instance or None if chain not found.
    """
    agent_names = config.agent_chains.get(chain_name)
    if not agent_names:
        return None

    return AgentChain(
        agents=agent_names,
        max_retries=config.general.max_retries,
        retry_delay_ms=config.general.retry_delay_ms,
        backoff_multiplier=config.general.backoff_multiplier,
        max_backoff_ms=config.general.max_backoff_ms,
    )
