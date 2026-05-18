"""AgentChainConfig Pydantic model."""

from __future__ import annotations

from pydantic import Field

from ralph.policy.models._frozen_policy_model import _FrozenPolicyModel


class AgentChainConfig(_FrozenPolicyModel):
    """Definition of a named agent fallback chain."""

    agents: list[str] = Field(..., min_length=1, description="Agents in fallback order")
    max_retries: int = Field(default=3, ge=0, description="Max retries per agent")
    retry_delay_ms: int = Field(default=1000, ge=0, description="Base retry delay in milliseconds")
