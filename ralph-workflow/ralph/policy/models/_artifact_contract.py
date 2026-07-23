"""ArtifactContract Pydantic model."""

from __future__ import annotations

from typing import cast

from pydantic import Field, model_validator

from ralph.policy.models._frozen_policy_model import _FrozenPolicyModel


class ArtifactContract(_FrozenPolicyModel):
    """Contract for an artifact type submitted by an agent at a given drain."""

    drain: str = Field(..., description="Drain this artifact is submitted at")
    artifact_type: str = Field(
        ...,
        description="Artifact type identifier submitted via MCP",
    )
    decision_vocabulary: list[str] = Field(
        default_factory=list,
        description="Valid decision values for analysis artifacts",
    )
    prompt_template: str | None = Field(
        default=None,
        description="Optional custom prompt template path",
    )
    markdown_summary_path: str | None = Field(
        default=None,
        description=(
            "Path to the human-readable markdown summary for this artifact. "
            "When set, the runner writes a markdown handoff at this path."
        ),
    )

    @model_validator(mode="before")
    @classmethod
    def _reject_phase_owned_fields(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        raw = cast("dict[str, object]", dict(data))
        if "artifact_required" in raw:
            raise ValueError(
                "ArtifactContract.artifact_required has moved to pipeline.toml. "
                "Set phases.<phase>.artifact_required instead."
            )
        return raw
