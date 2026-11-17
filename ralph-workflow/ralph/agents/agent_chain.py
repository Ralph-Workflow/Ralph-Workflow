from __future__ import annotations

import time
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from collections.abc import Callable


class AgentChain:
    def __init__(
        self,
        agents: list[str],
        max_retries: int = 3,
        retry_delay_ms: int = 1000,
        backoff_multiplier: float = 2.0,
        max_backoff_ms: int = 60000,
    ) -> None:
        self.agents = agents
        self.current_index = 0
        self.retries = 0
        self.max_retries = max_retries
        self.retry_delay_ms = retry_delay_ms
        self.backoff_multiplier = backoff_multiplier
        self.max_backoff_ms = max_backoff_ms

    @property
    def current_agent(self) -> str | None:
        if not self.agents or self.current_index >= len(self.agents):
            return None
        return self.agents[self.current_index]

    @property
    def is_exhausted(self) -> bool:
        return self.current_agent is None

    def can_retry(self) -> bool:
        return self.retries < self.max_retries

    def advance(self) -> bool:
        if self.current_index + 1 < len(self.agents):
            self.current_index += 1
            self.retries = 0
            logger.debug("Advanced to next agent: {}", self.current_agent)
            return True
        logger.debug("Agent chain exhausted")
        return False

    def record_retry(self) -> None:
        self.retries += 1
        logger.debug(
            "Retry {} of {} for agent {}",
            self.retries,
            self.max_retries,
            self.current_agent,
        )

    def calculate_backoff(self) -> float:
        delay = self.retry_delay_ms * (self.backoff_multiplier**self.retries)
        return min(delay, self.max_backoff_ms) / 1000.0

    def wait_backoff(self, *, _sleep: Callable[[float], None] = time.sleep) -> None:
        backoff = self.calculate_backoff()
        logger.debug("Backing off for {:.2f} seconds", backoff)
        _sleep(backoff)
