"""Pydantic models for policy configuration (agents.toml, pipeline.toml, artifacts.toml).

This module defines the validated data structures that represent Ralph's orchestration
policy. All three TOML files are modeled here with strict cross-field validation
to ensure policy consistency at startup.

The three policy documents are:
- agents.toml: Agent chain definitions and drain-to-chain bindings
- pipeline.toml: Phase graph with transitions driven by TOML config
- artifacts.toml: Artifact contracts per drain (MCP-artifact-only from day one)
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

# ---------------------------------------------------------------------------
# Shared types
# ---------------------------------------------------------------------------

DrainName = Literal[
    "planning",
    "development",
    "development_analysis",
    "development_commit",
    "review",
    "review_analysis",
    "review_commit",
    "fix",
    "complete",
]


# ---------------------------------------------------------------------------
# agents.toml models
# ---------------------------------------------------------------------------


class AgentDrainConfig(BaseModel):  # type: ignore[explicit-any]
    """Binding from a named drain to an agent chain.

    Attributes:
        chain: Name of the agent chain to invoke when this drain is active.
    """

    model_config = ConfigDict(frozen=True)

    chain: str = Field(..., description="Agent chain name to bind to this drain")


class AgentChainConfig(BaseModel):  # type: ignore[explicit-any]
    """Definition of a named agent fallback chain.

    Attributes:
        agents: Ordered list of agent names to try in sequence on failure.
        max_retries: Maximum retry attempts per agent before falling back.
        retry_delay_ms: Base delay between retries in milliseconds.
    """

    model_config = ConfigDict(frozen=True)

    agents: list[str] = Field(..., min_length=1, description="Agents in fallback order")
    max_retries: int = Field(default=3, ge=0, description="Max retries per agent")
    retry_delay_ms: int = Field(default=1000, ge=0, description="Base retry delay in milliseconds")


class AgentsPolicy(BaseModel):  # type: ignore[explicit-any]
    """Top-level agents.toml policy document.

    Attributes:
        agent_chains: Map of chain name -> chain definition.
        agent_drains: Map of drain name -> chain binding.
        forbid_sibling_drain_inference: If True, rejects implicit sibling-drain
            inheritance. Every built-in drain must have an explicit chain binding.
    """

    model_config = ConfigDict(frozen=True)

    agent_chains: dict[str, AgentChainConfig] = Field(
        default_factory=dict,
        description="Named agent chains available for binding",
    )
    agent_drains: dict[DrainName, AgentDrainConfig] = Field(
        default_factory=dict,
        description="Drain-to-chain bindings",
    )
    forbid_sibling_drain_inference: bool = Field(
        default=False,
        description="If True, reject implicit sibling-drain inheritance at startup",
    )

    @model_validator(mode="after")
    def drains_reference_known_chains(self) -> AgentsPolicy:
        """Ensure every drain binding references a chain that exists."""
        for drain, cfg in self.agent_drains.items():
            if cfg.chain not in self.agent_chains:
                raise ValueError(f"Drain '{drain}' references unknown chain '{cfg.chain}'")
        return self

    @model_validator(mode="after")
    def no_empty_chains(self) -> AgentsPolicy:
        """Ensure no chain has an empty agents list."""
        for name, cfg in self.agent_chains.items():
            if not cfg.agents:
                raise ValueError(f"Chain '{name}' has no agents")
        return self


# ---------------------------------------------------------------------------
# pipeline.toml models
# ---------------------------------------------------------------------------


class PhaseTransition(BaseModel):  # type: ignore[explicit-any]
    """Transition rules from a phase to other phases.

    Attributes:
        on_success: Phase to advance to on successful completion.
        on_failure: Phase to route to on failure (None = pipeline fails).
        on_loopback: Phase to route to when a loopback/continue signal is received.
    """

    model_config = ConfigDict(frozen=True)

    on_success: str = Field(..., description="Next phase on success")
    on_failure: str | None = Field(
        default=None, description="Next phase on failure (None = fail pipeline)"
    )
    on_loopback: str | None = Field(
        default=None,
        description="Next phase on loopback/continue signal (e.g., more iterations)",
    )


class PhaseDefinition(BaseModel):  # type: ignore[explicit-any]
    """Definition of a single phase in the pipeline graph.

    Attributes:
        drain: Which drain (agent chain binding) is active during this phase.
        transitions: Routing rules when phase completes.
        requires_commit: Whether this phase gates the commit decision.
        embeds_analysis: Whether this phase includes an embedded analysis step.
    """

    model_config = ConfigDict(frozen=True)

    drain: DrainName = Field(..., description="Drain binding for this phase")
    transitions: PhaseTransition = Field(..., description="Transition routing rules")
    requires_commit: bool = Field(
        default=False,
        description="Whether this phase must produce a commit artifact",
    )
    embeds_analysis: bool = Field(
        default=False,
        description="Whether this phase includes an embedded analysis decision",
    )
    prompt_template: str | None = Field(
        default=None,
        description="File-backed .jinja prompt template for this phase",
    )
    continuation_template: str | None = Field(
        default=None,
        description="Optional continuation .jinja prompt template for this phase",
    )


class PostCommitRouteWhen(BaseModel):  # type: ignore[explicit-any]
    """Condition selector for post-commit budget-guarded routing."""

    model_config = ConfigDict(frozen=True)

    phase: Literal["development_commit", "review_commit"] = Field(
        ...,
        description="Commit phase that this route applies to",
    )
    budget_state: Literal["remaining", "exhausted"] = Field(
        ...,
        description="Whether relevant budget remains (>0) or is exhausted (<=0)",
    )


class PostCommitRoute(BaseModel):  # type: ignore[explicit-any]
    """Budget-guarded route applied after commit success."""

    model_config = ConfigDict(frozen=True)

    when: PostCommitRouteWhen = Field(..., description="Route condition")
    target: str = Field(..., description="Target phase when condition matches")


class ParallelExecutionPolicy(BaseModel):  # type: ignore[explicit-any]
    """Policy controls for planning-artifact work_units fanout."""

    model_config = ConfigDict(frozen=True)

    source: Literal["planning_artifact_work_units"] = Field(
        default="planning_artifact_work_units",
        description="Source of parallel fanout declarations",
    )
    max_parallel_workers: int = Field(
        default=8,
        ge=1,
        description="Maximum allowed concurrent work units from planning artifact",
    )
    require_allowed_directories: bool = Field(
        default=True,
        description="Require each work unit to declare allowed_directories",
    )


class PipelinePolicy(BaseModel):  # type: ignore[explicit-any]
    """Top-level pipeline.toml policy document.

    Attributes:
        phases: Map of phase name -> phase definition.
        entry_phase: Name of the phase where the pipeline starts.
        terminal_phase: Name of the phase that marks successful completion.
    """

    model_config = ConfigDict(frozen=True)

    phases: dict[str, PhaseDefinition] = Field(
        default_factory=dict,
        description="All phases in the pipeline graph",
    )
    entry_phase: str = Field(
        default="planning",
        description="Phase where pipeline begins",
    )
    terminal_phase: str = Field(
        default="complete",
        description="Phase that marks successful pipeline completion",
    )
    post_commit_routes: list[PostCommitRoute] = Field(
        default_factory=list,
        description="Optional budget-guarded routes for commit success transitions",
    )
    parallel_execution: ParallelExecutionPolicy | None = Field(
        default=None,
        description="Optional planning-artifact parallel execution policy",
    )

    @model_validator(mode="after")
    def all_transitions_reference_known_phases(self) -> PipelinePolicy:
        """Ensure every transition target is a defined phase or terminal."""
        terminal_states = {self.terminal_phase, "failed"}
        for phase_name, phase_def in self.phases.items():
            t = phase_def.transitions
            for label, target in [
                ("on_success", t.on_success),
                ("on_failure", t.on_failure),
                ("on_loopback", t.on_loopback),
            ]:
                if (
                    target is not None
                    and target not in terminal_states
                    and target not in self.phases
                ):
                    raise ValueError(
                        f"Phase '{phase_name}' transitions.{label} references "
                        f"unknown phase '{target}'"
                    )
        return self

    @model_validator(mode="after")
    def entry_phase_exists(self) -> PipelinePolicy:
        """Ensure the entry phase is defined."""
        if self.entry_phase not in self.phases:
            raise ValueError(f"entry_phase '{self.entry_phase}' is not defined in phases")
        return self

    @model_validator(mode="after")
    def no_phase_cycles_without_loopback(self) -> PipelinePolicy:
        """Detect obvious infinite loop risks (phase transitions to itself without loopback).

        This is a shallow check; runtime budgets still govern actual loop limits.
        """
        for name, phase_def in self.phases.items():
            t = phase_def.transitions
            if t.on_success == name and t.on_loopback is None:
                raise ValueError(
                    f"Phase '{name}' transitions.on_success to itself with no "
                    f"on_loopback — this creates an infinite loop with no escape"
                )
        return self

    @model_validator(mode="after")
    def post_commit_routes_reference_known_targets(self) -> PipelinePolicy:
        """Ensure post_commit route targets are defined phases or terminal pseudo-phases."""
        terminal_states = {self.terminal_phase, "failed"}
        for route in self.post_commit_routes:
            if route.target not in terminal_states and route.target not in self.phases:
                raise ValueError(f"post_commit_routes target '{route.target}' is not a known phase")
        return self

    @model_validator(mode="after")
    def post_commit_routes_unique_conditions(self) -> PipelinePolicy:
        """Ensure there is at most one route per (phase, budget_state) pair."""
        seen: set[tuple[str, str]] = set()
        for route in self.post_commit_routes:
            key = (route.when.phase, route.when.budget_state)
            if key in seen:
                raise ValueError(
                    "Duplicate post_commit_routes condition for "
                    f"phase='{route.when.phase}', budget_state='{route.when.budget_state}'"
                )
            seen.add(key)
        return self


# ---------------------------------------------------------------------------
# artifacts.toml models
# ---------------------------------------------------------------------------


class ArtifactContract(BaseModel):  # type: ignore[explicit-any]
    """Contract for an artifact type submitted by an agent at a given drain.

    Attributes:
        drain: Which drain this artifact is submitted at.
        artifact_type: Type identifier for the artifact (e.g., planning_json).
        decision_vocabulary: Valid values for the decision field (for analysis drains).
        prompt_template: Optional template for generating prompts (None = use default).
    """

    model_config = ConfigDict(frozen=True)

    drain: DrainName = Field(..., description="Drain this artifact is submitted at")
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


class ArtifactsPolicy(BaseModel):  # type: ignore[explicit-any]
    """Top-level artifacts.toml policy document.

    Attributes:
        artifacts: Map of artifact name -> artifact contract.
    """

    model_config = ConfigDict(frozen=True)

    artifacts: dict[str, ArtifactContract] = Field(
        default_factory=dict,
        description="All artifact contracts keyed by artifact name",
    )

    @model_validator(mode="after")
    def no_duplicate_artifact_types(self) -> ArtifactsPolicy:
        """Ensure no two artifacts share the same drain + artifact_type pair."""
        seen: dict[tuple[DrainName, str], str] = {}
        for name, contract in self.artifacts.items():
            key = (contract.drain, contract.artifact_type)
            if key in seen:
                raise ValueError(
                    f"Artifacts '{name}' and '{seen[key]}' both declare "
                    f"drain='{contract.drain}', artifact_type='{contract.artifact_type}'"
                )
            seen[key] = name
        return self


# ---------------------------------------------------------------------------
# Convenience aggregate type
# ---------------------------------------------------------------------------


class PolicyBundle(BaseModel):  # type: ignore[explicit-any]
    """Aggregate of all three policy documents.

    This is what the loader returns after validating all three TOML files together.
    """

    model_config = ConfigDict(frozen=True)

    agents: AgentsPolicy = Field(..., description="Agent chains and drain bindings")
    pipeline: PipelinePolicy = Field(..., description="Phase graph and routing")
    artifacts: ArtifactsPolicy = Field(..., description="Artifact contracts per drain")

    @model_validator(mode="after")
    def all_pipeline_drains_are_bound(self) -> PolicyBundle:
        """Ensure every drain used in pipeline.phases is bound in agents.agent_drains.

        Skips the terminal phase since it never actually invokes an agent —
        the pipeline ends when it reaches terminal_phase.
        """
        unbound: list[DrainName] = []
        for phase_name, phase_def in self.pipeline.phases.items():
            # Skip terminal phase — it never invokes an agent
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
    def analysis_decision_vocabulary_present(self) -> PolicyBundle:
        """Ensure analysis phases have decision_vocabulary defined."""
        analysis_phases = {
            name: defn for name, defn in self.pipeline.phases.items() if defn.embeds_analysis
        }
        for phase_name, phase_def in analysis_phases.items():
            matching_artifacts = [
                art for art in self.artifacts.artifacts.values() if art.drain == phase_def.drain
            ]
            if not any(a.decision_vocabulary for a in matching_artifacts):
                raise ValueError(
                    f"Phase '{phase_name}' embeds_analysis=true but no matching "
                    f"artifact contract has a decision_vocabulary defined"
                )
        return self
