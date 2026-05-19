"""AgentDrainConfig Pydantic model."""

from __future__ import annotations

from pydantic import Field, model_validator

from ralph.policy.models._frozen_policy_model import _FrozenPolicyModel


class AgentDrainConfig(_FrozenPolicyModel):
    """Binding from a named drain to an agent chain."""

    chain: str = Field(..., description="Agent chain name to bind to this drain")
    drain_class: str | None = Field(
        default=None,
        description=(
            "Drain capability class — one of planning|development|analysis|review|fix|commit. "
            "Required when forbid_sibling_drain_inference=true. "
            "Explicit drain_class always takes precedence over enum-based inference. "
            "Set this to declare the workflow role classifier for this drain."
        ),
    )
    capability_class: str | None = Field(
        default=None,
        description=(
            "MCP capability set override — one of planning|development|analysis|review|fix|commit. "
            "When None, falls back to drain_class for capability planning. "
            "Use this to decouple the workflow role from the MCP capability surface."
        ),
    )

    @model_validator(mode="after")
    def _validate_drain_class_value(self) -> AgentDrainConfig:
        allowed = {"planning", "development", "analysis", "review", "fix", "commit"}
        if self.drain_class is not None and self.drain_class not in allowed:
            raise ValueError(
                f"drain_class '{self.drain_class}' is not valid; "
                f"must be one of: {', '.join(sorted(allowed))}"
            )
        if self.capability_class is not None and self.capability_class not in allowed:
            raise ValueError(
                f"capability_class '{self.capability_class}' is not valid; "
                f"must be one of: {', '.join(sorted(allowed))}"
            )
        return self
