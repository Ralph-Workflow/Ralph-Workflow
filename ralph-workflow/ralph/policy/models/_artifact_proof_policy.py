"""ArtifactProofPolicy Pydantic model."""

from __future__ import annotations

from pydantic import ConfigDict, Field

from ralph.policy.models._frozen_policy_model import _FrozenPolicyModel


class ArtifactProofPolicy(_FrozenPolicyModel):
    """Per-phase proof requirements for development artifacts."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    require_plan_proof: bool = Field(
        default=True,
        description=(
            "When True, validate plan_items_proven coverage for the plan artifact's "
            "canonical step refs or assigned work unit ids."
        ),
    )
    require_analysis_proof: bool = Field(
        default=True,
        description=(
            "When True, validate analysis_items_addressed coverage for prior how_to_fix "
            "items when analysis feedback exists."
        ),
    )
