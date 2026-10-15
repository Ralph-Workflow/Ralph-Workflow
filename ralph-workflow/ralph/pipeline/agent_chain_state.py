"""Agent chain state model for pipeline state."""

from __future__ import annotations

from pydantic import ConfigDict, Field

from ralph.pydantic_compat import RalphBaseModel

_FROZEN = ConfigDict(frozen=True)


class AgentChainState(RalphBaseModel):
    """State for agent fallback chain management.

    Attributes:
        agents: List of agent names in the fallback chain.
        current_index: Current agent index being used.
        retries: Number of retries for current agent.
    """

    model_config = _FROZEN

    agents: list[str] = Field(default_factory=list)
    current_index: int = 0
    retries: int = 0
    progress_guard: dict[str, object] | None = None

    def with_retry_increment(
        self,
        progress_guard: dict[str, object] | None = None,
    ) -> AgentChainState:
        """Return a copy with retries incremented by 1."""
        return AgentChainState(
            agents=self.agents,
            current_index=self.current_index,
            retries=self.retries + 1,
            progress_guard=progress_guard if progress_guard is not None else self.progress_guard,
        )

    def with_advance(self) -> AgentChainState:
        """Return a copy advanced to the next agent with retries reset to 0."""
        return AgentChainState(
            agents=self.agents,
            current_index=self.current_index + 1,
            retries=0,
            progress_guard=None,
        )

    def with_progress_guard(
        self,
        progress_guard: dict[str, object] | None,
    ) -> AgentChainState:
        """Return a copy with progress_guard updated."""
        return AgentChainState(
            agents=self.agents,
            current_index=self.current_index,
            retries=self.retries,
            progress_guard=progress_guard,
        )
