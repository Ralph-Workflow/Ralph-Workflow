"""Agent fallback chain with retry and backoff behavior."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from collections.abc import Callable


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

    def wait_backoff(self, *, _sleep: Callable[[float], None] = time.sleep) -> None:
        """Wait for the backoff period."""
        backoff = self.calculate_backoff()
        logger.debug("Backing off for {:.2f} seconds", backoff)
        _sleep(backoff)
