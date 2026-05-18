"""AgentsPolicy Pydantic model."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import Field, model_validator

from ralph.policy.models._frozen_policy_model import _FrozenPolicyModel

if TYPE_CHECKING:
    from ralph.policy.models._agent_chain_config import AgentChainConfig
    from ralph.policy.models._agent_drain_config import AgentDrainConfig


class AgentsPolicy(_FrozenPolicyModel):
    """Top-level agents.toml policy document."""

    agent_chains: dict[str, AgentChainConfig] = Field(
        default_factory=dict,
        description="Named agent chains available for binding",
    )
    agent_drains: dict[str, AgentDrainConfig] = Field(
        default_factory=dict,
        description="Drain-to-chain bindings",
    )
    forbid_sibling_drain_inference: bool = Field(
        default=False,
        description="If True, reject implicit sibling-drain inheritance at startup",
    )

    @model_validator(mode="after")
    def drains_reference_known_chains(self) -> AgentsPolicy:
        for drain, cfg in self.agent_drains.items():
            if cfg.chain not in self.agent_chains:
                raise ValueError(f"Drain '{drain}' references unknown chain '{cfg.chain}'")
        return self

    @model_validator(mode="after")
    def no_empty_chains(self) -> AgentsPolicy:
        for name, cfg in self.agent_chains.items():
            if not cfg.agents:
                raise ValueError(f"Chain '{name}' has no agents")
        return self
