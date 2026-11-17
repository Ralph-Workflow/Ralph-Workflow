from typing import Self

from pydantic import Field, model_validator

from ralph.policy.models._artifact_contract import ArtifactContract
from ralph.policy.models._frozen_policy_model import _FrozenPolicyModel


class ArtifactsPolicy(_FrozenPolicyModel):
    artifacts: dict[str, ArtifactContract] = Field(
        default_factory=dict,
        description="All artifact contracts keyed by artifact name",
    )

    @model_validator(mode="after")
    def one_artifact_type_per_drain(self) -> Self:
        seen: dict[str, tuple[str, str]] = {}
        for name, contract in self.artifacts.items():
            first = seen.setdefault(contract.drain, (name, contract.artifact_type))
            if first[1] != contract.artifact_type or first[0] != name:
                raise ValueError(
                    f"Artifacts '{first[0]}' and '{name}' both claim drain "
                    f"'{contract.drain}' with artifact types '{first[1]}' and "
                    f"'{contract.artifact_type}'. A drain is graded on exactly one "
                    "artifact type, so a stale contract left in a project or global "
                    "artifacts.toml would silently override the default. Remove or "
                    "rename the obsolete contract."
                )
        return self
