from __future__ import annotations

from pydantic import ConfigDict, Field

from ralph.pydantic_compat import RalphBaseModel


class AgentChainConfig(RalphBaseModel):
    model_config = ConfigDict(frozen=True)
    agents: list[str] = Field(..., min_length=1, description="Agents in fallback order")
    max_retries: int = Field(default=3, ge=0, description="Max retries per agent")
    retry_delay_ms: int = Field(default=1000, ge=0, description="Base retry delay in milliseconds")
