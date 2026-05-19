"""LoopCounterConfig Pydantic model."""

from __future__ import annotations

from pydantic import Field

from ralph.policy.models._frozen_policy_model import _FrozenPolicyModel


class LoopCounterConfig(_FrozenPolicyModel):
    """Declaration of a named loop iteration counter in the pipeline."""

    default_max: int = Field(default=3, ge=0, description="Default maximum iterations")
    description: str = Field(default="", description="Human-readable description")
