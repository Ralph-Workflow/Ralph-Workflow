"""ArtifactsPolicy Pydantic model."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import Field, model_validator

from ralph.policy.models._frozen_policy_model import _FrozenPolicyModel

if TYPE_CHECKING:
    from ralph.policy.models._artifact_contract import ArtifactContract


class ArtifactsPolicy(_FrozenPolicyModel):
    """Top-level artifacts.toml policy document."""

    artifacts: dict[str, ArtifactContract] = Field(
        default_factory=dict,
        description="All artifact contracts keyed by artifact name",
    )

    @model_validator(mode="after")
    def no_duplicate_artifact_types(self) -> ArtifactsPolicy:
        seen: dict[tuple[str, str], str] = {}
        for name, contract in self.artifacts.items():
            key = (contract.drain, contract.artifact_type)
            if key in seen:
                raise ValueError(
                    f"Artifacts '{name}' and '{seen[key]}' both declare "
                    f"drain='{contract.drain}', artifact_type='{contract.artifact_type}'"
                )
            seen[key] = name
        return self
