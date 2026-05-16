"""Pydantic v2 models for Ralph configuration."""

from __future__ import annotations

from pydantic import ConfigDict, Field, model_validator

from ralph.policy.models import AgentChainConfig, AgentDrainConfig
from ralph.pydantic_compat import RalphBaseModel

from .agent_config import AgentConfig
from .ccs_config import CcsAliasConfig, CcsConfig
from .general_config import GeneralConfig, GeneralWorkflowFlags


class _FrozenConfigModel(RalphBaseModel):
    """Private base for frozen configuration models."""

    model_config = ConfigDict(frozen=True)


def _normalize_chain_value(value: object) -> AgentChainConfig:
    if isinstance(value, AgentChainConfig):
        return value
    if isinstance(value, list):
        return AgentChainConfig(agents=value)
    if isinstance(value, dict):
        return AgentChainConfig(
            agents=value.get("agents", []),
            max_retries=value.get("max_retries", 3),
            retry_delay_ms=value.get("retry_delay_ms", 1000),
        )
    return AgentChainConfig(agents=[str(value)])


def _normalize_drain_value(value: object) -> AgentDrainConfig:
    if isinstance(value, str):
        return AgentDrainConfig(chain=value)
    if isinstance(value, dict):
        return AgentDrainConfig(
            chain=value.get("chain", ""),
            drain_class=value.get("drain_class"),
            capability_class=value.get("capability_class"),
        )
    return AgentDrainConfig(chain=str(value))


class UnifiedConfig(_FrozenConfigModel):
    """Top-level merged configuration (global + local + CLI overrides)."""

    general: GeneralConfig = Field(default_factory=GeneralConfig)
    ccs: CcsConfig = Field(default_factory=CcsConfig)
    agents: dict[str, AgentConfig] = Field(default_factory=dict)
    ccs_aliases: dict[str, str | CcsAliasConfig] = Field(default_factory=dict)
    agent_chains: dict[str, AgentChainConfig] = Field(default_factory=dict)
    agent_drains: dict[str, AgentDrainConfig] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _normalize_agent_chains_and_drains(cls, data: object) -> object:
        """Accept both flat format (list[str]/str) and rich format for backward compat."""
        if not isinstance(data, dict):
            return data

        normalized_data: dict[str, object] = {
            name: value for name, value in data.items() if isinstance(name, str)
        }

        chains = normalized_data.get("agent_chains")
        if isinstance(chains, dict):
            normalized_chains: dict[str, object] = {}
            for name, value in chains.items():
                if not isinstance(name, str):
                    continue
                normalized_chains[name] = _normalize_chain_value(value)
            normalized_data["agent_chains"] = normalized_chains

        drains = normalized_data.get("agent_drains")
        if isinstance(drains, dict):
            normalized_drains: dict[str, object] = {}
            for name, value in drains.items():
                if not isinstance(name, str):
                    continue
                normalized_drains[name] = _normalize_drain_value(value)
            normalized_data["agent_drains"] = normalized_drains

        return normalized_data

    @model_validator(mode="after")
    def _validate_drain_references(self) -> UnifiedConfig:
        """Ensure every drain references an existing chain."""
        for drain_name, drain_cfg in self.agent_drains.items():
            if drain_cfg.chain not in self.agent_chains:
                raise ValueError(
                    f"Drain '{drain_name}' references unknown chain '{drain_cfg.chain}'"
                )
        return self


__all__ = [
    "AgentConfig",
    "CcsAliasConfig",
    "CcsConfig",
    "GeneralConfig",
    "GeneralWorkflowFlags",
    "UnifiedConfig",
]
