"""ArtifactProofPolicy Pydantic model."""

from __future__ import annotations

from typing import cast

from pydantic import ConfigDict, Field, model_validator

from ralph.policy.models._frozen_policy_model import _FrozenPolicyModel


class ArtifactProofPolicy(_FrozenPolicyModel):
    """Per-phase proof requirements for development artifacts.

    Plan-shape coverage (step IDs / work-unit IDs) is no longer gated here:
    the development phase accepts any completed ``development_result`` whose
    ``plan_items_proven`` cite reproducible evidence for the work done, so the
    plan's intent is followed through the development-analysis feedback loop
    (see U-2) rather than a one-shot exact match against a parsed plan. The
    analysis-finding coverage remains because every prior
    ``What Came Up Short`` finding is a stable ID and a completion claim that
    omits a known one is a documented fault, not a shape mismatch.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    require_analysis_proof: bool = Field(
        default=True,
        description=(
            "When True, validate analysis_items_addressed coverage for prior localized "
            "analysis findings when analysis feedback exists."
        ),
    )

    @model_validator(mode="before")
    @classmethod
    def _discard_legacy_plan_proof(cls, data: object) -> object:
        if not isinstance(data, dict) or "require_plan_proof" not in data:
            return data
        raw = cast("dict[str, object]", data)
        if not isinstance(raw["require_plan_proof"], bool):
            raise ValueError("Legacy require_plan_proof must be a boolean; remove this setting.")
        return {key: value for key, value in raw.items() if key != "require_plan_proof"}
