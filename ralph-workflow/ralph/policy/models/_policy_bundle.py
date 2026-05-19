"""PolicyBundle Pydantic model."""

from typing import Self

from pydantic import Field, model_validator

from ralph.policy.models._agents_policy import AgentsPolicy
from ralph.policy.models._artifacts_policy import ArtifactsPolicy
from ralph.policy.models._frozen_policy_model import _FrozenPolicyModel
from ralph.policy.models._pipeline_policy import PipelinePolicy


class PolicyBundle(_FrozenPolicyModel):
    """Aggregate of all three policy documents."""

    agents: AgentsPolicy = Field(..., description="Agent chains and drain bindings")
    pipeline: PipelinePolicy = Field(..., description="Phase graph and routing")
    artifacts: ArtifactsPolicy = Field(..., description="Artifact contracts per drain")

    @model_validator(mode="after")
    def all_pipeline_drains_are_bound(self) -> Self:
        unbound: list[str] = []
        for phase_name, phase_def in self.pipeline.phases.items():
            if phase_def.role == "terminal":
                continue
            if phase_name == self.pipeline.terminal_phase:
                continue
            if phase_def.drain not in self.agents.agent_drains:
                unbound.append(phase_def.drain)
        if unbound:
            raise ValueError(
                f"Pipeline uses unbound drains: {sorted(set(unbound))}. "
                f"Each drain must have a binding in agents.agent_drains."
            )
        return self

    @model_validator(mode="after")
    def analysis_decision_vocabulary_present(self) -> Self:
        analysis_phases = {
            name: defn for name, defn in self.pipeline.phases.items() if defn.role == "analysis"
        }
        for phase_name, phase_def in analysis_phases.items():
            matching_artifacts = [
                art for art in self.artifacts.artifacts.values() if art.drain == phase_def.drain
            ]
            if not any(a.decision_vocabulary for a in matching_artifacts):
                raise ValueError(
                    f"Phase '{phase_name}' has role='analysis' but no matching "
                    f"artifact contract has a decision_vocabulary defined"
                )
        return self
