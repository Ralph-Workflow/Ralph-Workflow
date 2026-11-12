from __future__ import annotations

from pydantic import ConfigDict, Field

from ralph.pydantic_compat import RalphBaseModel

_FROZEN = ConfigDict(frozen=True)


class AgentChainState(RalphBaseModel):
    model_config = _FROZEN

    agents: list[str] = Field(default_factory=list)
    current_index: int = 0
    retries: int = 0

    def with_retry_increment(self) -> AgentChainState:
        return AgentChainState(
            agents=self.agents,
            current_index=self.current_index,
            retries=self.retries + 1,
        )

    def with_advance(self) -> AgentChainState:
        return AgentChainState(
            agents=self.agents,
            current_index=self.current_index + 1,
            retries=0,
        )
