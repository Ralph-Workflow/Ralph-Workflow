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

from typing import Literal, cast

from loguru import logger
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

PhaseRole = Literal[
    "execution",
    "analysis",
    "review",
    "commit",
    "verification",
    "terminal",
    "fanout_join",
]


class _FrozenPolicyModel(BaseModel):  # type: ignore[explicit-any]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library
    """Private base for frozen policy models.

    Owns ``model_config = ConfigDict(frozen=True)`` once so descendants do not
    repeat it. Pydantic v2 inherits ``model_config`` when descendants do not
    declare one of their own.
    """

    model_config = ConfigDict(frozen=True)


# ---------------------------------------------------------------------------
# agents.toml models
# ---------------------------------------------------------------------------


class AgentDrainConfig(_FrozenPolicyModel):  # type: ignore[explicit-any]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library
    """Binding from a named drain to an agent chain.

    Attributes:
        chain: Name of the agent chain to invoke when this drain is active.
    """

    chain: str = Field(..., description="Agent chain name to bind to this drain")


class AgentChainConfig(_FrozenPolicyModel):  # type: ignore[explicit-any]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library
    """Definition of a named agent fallback chain.

    Attributes:
        agents: Ordered list of agent names to try in sequence on failure.
        max_retries: Maximum retry attempts per agent before falling back.
        retry_delay_ms: Base delay between retries in milliseconds.
    """

    agents: list[str] = Field(..., min_length=1, description="Agents in fallback order")
    max_retries: int = Field(default=3, ge=0, description="Max retries per agent")
    retry_delay_ms: int = Field(default=1000, ge=0, description="Base retry delay in milliseconds")


class AgentsPolicy(_FrozenPolicyModel):  # type: ignore[explicit-any]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library
    """Top-level agents.toml policy document.

    Attributes:
        agent_chains: Map of chain name -> chain definition.
        agent_drains: Map of drain name -> chain binding.
        forbid_sibling_drain_inference: If True, rejects implicit sibling-drain
            inheritance. Every built-in drain must have an explicit chain binding.
    """

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
# pipeline.toml sub-models for workflow-semantic fields
# ---------------------------------------------------------------------------


class PhaseRetryPolicy(_FrozenPolicyModel):  # type: ignore[explicit-any]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library
    """Per-phase retry policy overriding chain-level defaults.

    Attributes:
        max_retries: Maximum retries for this phase.
        retry_delay_ms: Base retry delay in milliseconds.
        retry_in_session: Whether to preserve session on retry.
    """

    max_retries: int = Field(default=3, ge=0)
    retry_delay_ms: int = Field(default=1000, ge=0)
    retry_in_session: bool = False


class PhaseLoopPolicy(_FrozenPolicyModel):  # type: ignore[explicit-any]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library
    """Loop bounds for analysis phases.

    Attributes:
        max_iterations: Maximum analysis loop iterations before forcing loopback.
        iteration_state_field: Key in PipelineState.loop_iterations tracking this phase's counter.
        loopback_review_outcome: When set, loopback transitions set review_outcome to this value.
    """

    max_iterations: int = Field(..., ge=0)
    iteration_state_field: str = Field(...)
    loopback_review_outcome: str | None = None


class PhaseDecisionRoute(_FrozenPolicyModel):  # type: ignore[explicit-any]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library
    """Route produced by an analysis decision.

    Attributes:
        target: Phase to route to when this decision is received.
        reset_loop: Whether to reset the analysis loop counter on this transition.
    """

    target: str = Field(...)
    reset_loop: bool = False


class PhaseCommitPolicy(_FrozenPolicyModel):  # type: ignore[explicit-any]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library
    """Commit semantics for commit-role phases.

    Attributes:
        requires_artifact: Whether commit_message artifact is required.
        skipped_advances_progress: Whether a skipped commit still advances routing.
        increments_counter: Key in pipeline.budget_counters to bump on non-skipped commit.
            Use None for no-op (no counter incremented).
        loop_resets: List of loop iteration state fields to reset after commit.
    """

    requires_artifact: bool = True
    skipped_advances_progress: bool = True
    increments_counter: str | None = Field(
        default=None,
        description=(
            "Budget counter key (declared in pipeline.budget_counters) to bump on "
            "non-skipped commit. None means no counter is incremented."
        ),
    )
    loop_resets: list[str] = Field(default_factory=list)


class PhaseVerificationPolicy(_FrozenPolicyModel):  # type: ignore[explicit-any]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library
    """Verification gating semantics for a phase.

    Attributes:
        kind: Kind of verification (artifact, make_target, or none).
        gate_for: What this verification gates (advancement, completion, release).
        on_failure_route: Phase to route to on verification failure (None = fail pipeline).
    """

    kind: Literal["artifact", "make_target", "none"]
    gate_for: Literal["advancement", "completion", "release"]
    on_failure_route: str | None = None


class LoopCounterConfig(_FrozenPolicyModel):  # type: ignore[explicit-any]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library
    """Declaration of a named loop iteration counter in the pipeline.

    Loop counters track how many times an analysis phase has looped back.
    They are keyed by the value used in PhaseLoopPolicy.iteration_state_field.

    Attributes:
        default_max: Default maximum iterations (overridable via config).
        description: Human-readable description of this counter's purpose.
    """

    default_max: int = Field(default=3, ge=0, description="Default maximum iterations")
    description: str = Field(default="", description="Human-readable description")


class BudgetCounterConfig(_FrozenPolicyModel):  # type: ignore[explicit-any]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library
    """Declaration of a named budget counter in the pipeline.

    Budget counters track outer-progress (completed cycles) and remaining budget.
    They are keyed by the value used in PhaseCommitPolicy.increments_counter.

    Attributes:
        description: Human-readable description of this counter's purpose.
        tracks_budget: Whether remaining budget is tracked (True = exhaustion matters).
    """

    description: str = Field(default="", description="Human-readable description")
    tracks_budget: bool = Field(
        default=True,
        description="Whether remaining budget is tracked for post-commit routing",
    )


class RecoveryPolicy(_FrozenPolicyModel):  # type: ignore[explicit-any]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library
    """Pipeline-wide recovery policy.

    Attributes:
        cycle_cap: Maximum full-chain exhaustion cycles before exit.
        terminal_recovery_route: How terminal failures are routed.
        preserve_session_on_categories: Failure categories that preserve agent session.
    """

    cycle_cap: int = Field(default=200, ge=1)
    terminal_recovery_route: str = Field(
        default="failed",
        description=(
            "How terminal failures are routed. "
            "'failed', 'phase_failed', and 'exit_failure' are built-in pseudo-phases; "
            "any declared pipeline phase name is also valid."
        ),
    )
    preserve_session_on_categories: tuple[str, ...] = ("agent",)


# ---------------------------------------------------------------------------
# pipeline.toml models
# ---------------------------------------------------------------------------


class PhaseTransition(_FrozenPolicyModel):  # type: ignore[explicit-any]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library
    """Transition rules from a phase to other phases.

    Attributes:
        on_success: Phase to advance to on successful completion.
        on_failure: Phase to route to on failure (None = pipeline fails).
        on_loopback: Phase to route to when a loopback/continue signal is received.
    """

    on_success: str = Field(..., description="Next phase on success")
    on_failure: str | None = Field(
        default=None, description="Next phase on failure (None = fail pipeline)"
    )
    on_loopback: str | None = Field(
        default=None,
        description="Next phase on loopback/continue signal (e.g., more iterations)",
    )


class PhaseDefinition(_FrozenPolicyModel):  # type: ignore[explicit-any]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library
    """Definition of a single phase in the pipeline graph.

    Attributes:
        drain: Which drain (agent chain binding) is active during this phase.
        transitions: Routing rules when phase completes.
        role: Phase role classifying its behavior contract. REQUIRED for new configs.
        skip_invocation: When True, routing proceeds without invoking an agent.
        retry_policy: Optional per-phase retry policy overriding chain defaults.
        loop_policy: Required when role='analysis'; declares loop bounds.
        decisions: Decision vocabulary routing map; required when role='analysis'.
        commit_policy: Required when role='commit'; declares commit semantics.
        verification: Optional verification gating policy.
        terminal_outcome: Explicit terminal outcome; required when role='terminal'.
        bypass_routes: Named bypass routes (e.g. review_clean -> review_commit).
        requires_commit: Deprecated. Use role='commit' instead.
        embeds_analysis: Deprecated. Use role='analysis' instead.
        prompt_template: File-backed .jinja prompt template for this phase.
        continuation_template: Optional continuation .jinja prompt template.
    """

    drain: DrainName = Field(..., description="Drain binding for this phase")
    transitions: PhaseTransition = Field(..., description="Transition routing rules")

    # New workflow-semantic fields
    role: PhaseRole | None = Field(
        default=None,
        description="Phase role classifying behavior contract",
    )
    skip_invocation: bool = Field(
        default=False,
        description=(
            "When True, the runtime routes directly without invoking an agent. "
            "Useful for pass-through or routing-only phases."
        ),
    )
    retry_policy: PhaseRetryPolicy | None = Field(
        default=None,
        description="Per-phase retry policy override",
    )
    loop_policy: PhaseLoopPolicy | None = Field(
        default=None,
        description="Loop bounds for analysis phases",
    )
    decisions: dict[str, PhaseDecisionRoute] = Field(
        default_factory=dict,
        description="Analysis decision routing map",
    )
    commit_policy: PhaseCommitPolicy | None = Field(
        default=None,
        description="Commit semantics for commit phases",
    )
    verification: PhaseVerificationPolicy | None = Field(
        default=None,
        description="Verification gating policy",
    )
    terminal_outcome: Literal["success", "failure"] | None = Field(
        default=None,
        description="Explicit terminal outcome declaration",
    )
    bypass_routes: dict[str, str] = Field(
        default_factory=dict,
        description="Named bypass routes (outcome -> target phase)",
    )

    # Legacy fields — deprecated, use role instead
    requires_commit: bool = Field(
        default=False,
        description="Deprecated: use role='commit'.",
    )
    embeds_analysis: bool = Field(
        default=False,
        description="Deprecated: use role='analysis'.",
    )

    prompt_template: str | None = Field(
        default=None,
        description="File-backed .jinja prompt template for this phase",
    )
    continuation_template: str | None = Field(
        default=None,
        description="Optional continuation .jinja prompt template for this phase",
    )

    @model_validator(mode="before")
    @classmethod
    def _derive_role_from_legacy_flags(cls, data: object) -> object:
        """Derive role from deprecated embeds_analysis/requires_commit when role is unset."""
        if not isinstance(data, dict):
            return data
        d: dict[str, object] = cast("dict[str, object]", data)
        if d.get("role") is not None:
            return d
        if d.get("embeds_analysis"):
            logger.warning(
                "Phase uses deprecated 'embeds_analysis=true'; set role='analysis' instead"
            )
            return {**d, "role": "analysis"}
        if d.get("requires_commit"):
            logger.warning(
                "Phase uses deprecated 'requires_commit=true'; set role='commit' instead"
            )
            return {**d, "role": "commit"}
        return d


class PostCommitRouteWhen(_FrozenPolicyModel):  # type: ignore[explicit-any]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library
    """Condition selector for post-commit budget-guarded routing."""

    phase: str = Field(
        ...,
        description="Commit phase that this route applies to",
    )
    budget_state: Literal["remaining", "exhausted", "no_review"] = Field(
        ...,
        description=(
            "Budget state label: 'remaining' (budget>0), 'exhausted' (dev done, review remains), "
            "'no_review' (all budgets exhausted, no review to run)"
        ),
    )


class PostCommitRoute(_FrozenPolicyModel):  # type: ignore[explicit-any]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library
    """Budget-guarded route applied after commit success."""

    when: PostCommitRouteWhen = Field(..., description="Route condition")
    target: str = Field(..., description="Target phase when condition matches")


class ParallelExecutionPolicy(_FrozenPolicyModel):  # type: ignore[explicit-any]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library
    """Policy controls for planning-artifact work_units fanout."""

    source: Literal["planning_artifact_work_units"] = Field(
        default="planning_artifact_work_units",
        description="Source of parallel fanout declarations",
    )
    phase: str = Field(
        default="development",
        description="Phase that consumes planning work_units via parallel fanout",
    )
    max_parallel_workers: int = Field(
        default=8,
        ge=1,
        description="Maximum allowed concurrent work units from planning artifact",
    )
    max_work_units: int = Field(
        default=50,
        ge=1,
        description="Maximum allowed total work units from planning artifact",
    )
    require_allowed_directories: bool = Field(
        default=True,
        description="Require each work unit to declare allowed_directories",
    )
    post_fanout_verification: bool = Field(
        default=False,
        description=(
            "When True, run a serialized workspace-wide verification step after all "
            "parallel workers complete. Defaults to False so unit tests never invoke "
            "make verify."
        ),
    )


class PipelinePolicy(_FrozenPolicyModel):  # type: ignore[explicit-any]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library
    """Top-level pipeline.toml policy document.

    Attributes:
        phases: Map of phase name -> phase definition.
        entry_phase: Name of the phase where the pipeline starts.
        terminal_phase: Name of the phase that marks successful completion.
        loop_counters: Policy-declared loop iteration counters keyed by field name.
        budget_counters: Policy-declared budget counters keyed by counter name.
        default_phase_retry_policy: Default retry policy for phases without explicit retry_policy.
        recovery: Pipeline-wide recovery policy.
    """

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
    loop_counters: dict[str, LoopCounterConfig] = Field(
        default_factory=dict,
        description=(
            "Policy-declared loop iteration counters. "
            "Keys must match iteration_state_field values used in phase loop_policy."
        ),
    )
    budget_counters: dict[str, BudgetCounterConfig] = Field(
        default_factory=dict,
        description=(
            "Policy-declared budget counters. "
            "Keys must match increments_counter values used in phase commit_policy."
        ),
    )
    post_commit_routes: list[PostCommitRoute] = Field(
        default_factory=list,
        description="Optional budget-guarded routes for commit success transitions",
    )
    parallel_execution: ParallelExecutionPolicy | None = Field(
        default=None,
        description="Optional planning-artifact parallel execution policy",
    )
    default_phase_retry_policy: PhaseRetryPolicy = Field(
        default_factory=PhaseRetryPolicy,
        description="Default retry policy for phases without explicit retry_policy",
    )
    recovery: RecoveryPolicy = Field(
        default_factory=RecoveryPolicy,
        description="Pipeline-wide recovery configuration",
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
        """Detect obvious infinite loop risks (phase transitions to itself without loopback)."""
        for name, phase_def in self.phases.items():
            if name == self.terminal_phase:
                continue
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

    @model_validator(mode="after")
    def parallel_execution_phase_exists(self) -> PipelinePolicy:
        """Ensure the configured fanout phase exists and is non-terminal."""
        parallel = self.parallel_execution
        if parallel is None:
            return self
        if parallel.phase not in self.phases:
            raise ValueError(
                f"parallel_execution.phase '{parallel.phase}' is not a known phase"
            )
        phase_def = self.phases[parallel.phase]
        if phase_def.role == "terminal":
            raise ValueError("parallel_execution.phase cannot target a terminal phase")
        return self

    @model_validator(mode="after")
    def decision_routes_target_known_phases(self) -> PipelinePolicy:
        """Ensure every PhaseDecisionRoute.target resolves to a known phase or terminal."""
        terminal_states = {self.terminal_phase, "failed", "complete"}
        for phase_name, phase_def in self.phases.items():
            for decision_name, route in phase_def.decisions.items():
                if route.target not in terminal_states and route.target not in self.phases:
                    raise ValueError(
                        f"Phase '{phase_name}' decisions['{decision_name}'] targets "
                        f"unknown phase '{route.target}'"
                    )
        return self

    @model_validator(mode="after")
    def bypass_routes_target_known_phases(self) -> PipelinePolicy:
        """Ensure every bypass_route target resolves to a known phase or terminal."""
        terminal_states = {self.terminal_phase, "failed", "complete"}
        for phase_name, phase_def in self.phases.items():
            for outcome, target in phase_def.bypass_routes.items():
                if target not in terminal_states and target not in self.phases:
                    raise ValueError(
                        f"Phase '{phase_name}' bypass_routes['{outcome}'] targets "
                        f"unknown phase '{target}'"
                    )
        return self

    @model_validator(mode="after")
    def loop_counter_references_valid(self) -> PipelinePolicy:
        """Ensure loop_policy.iteration_state_field references a declared loop_counter.

        Only enforced when loop_counters is non-empty (populated by new-style config).
        Legacy configs without loop_counters are accepted for backward compatibility.
        """
        if not self.loop_counters:
            return self
        for phase_name, phase_def in self.phases.items():
            if phase_def.loop_policy is not None:
                field = phase_def.loop_policy.iteration_state_field
                if field not in self.loop_counters:
                    raise ValueError(
                        f"phases.{phase_name}.loop_policy.iteration_state_field: "
                        f"'{field}' is not declared in loop_counters. "
                        f"Declared counters: {sorted(self.loop_counters.keys())}. "
                        f"Add [loop_counters.{field}] to pipeline.toml."
                    )
        return self

    @model_validator(mode="after")
    def budget_counter_references_valid(self) -> PipelinePolicy:
        """Ensure commit_policy.increments_counter references a declared budget_counter.

        Only enforced when budget_counters is non-empty (populated by new-style config).
        """
        if not self.budget_counters:
            return self
        for phase_name, phase_def in self.phases.items():
            if phase_def.commit_policy is not None:
                counter = phase_def.commit_policy.increments_counter
                if counter is not None and counter not in self.budget_counters:
                    raise ValueError(
                        f"phases.{phase_name}.commit_policy.increments_counter: "
                        f"'{counter}' is not declared in budget_counters. "
                        f"Declared counters: {sorted(self.budget_counters.keys())}. "
                        f"Add [budget_counters.{counter}] to pipeline.toml."
                    )
        return self

    def effective_retry_policy(self, phase_name: str) -> PhaseRetryPolicy:
        """Resolve the effective retry policy for a phase."""
        phase_def = self.phases.get(phase_name)
        if phase_def is not None and phase_def.retry_policy is not None:
            return phase_def.retry_policy
        return self.default_phase_retry_policy


# ---------------------------------------------------------------------------
# artifacts.toml models
# ---------------------------------------------------------------------------


class ArtifactContract(_FrozenPolicyModel):  # type: ignore[explicit-any]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library
    """Contract for an artifact type submitted by an agent at a given drain.

    Attributes:
        drain: Which drain this artifact is submitted at.
        artifact_type: Type identifier for the artifact (e.g., planning_json).
        decision_vocabulary: Valid values for the decision field (for analysis drains).
        prompt_template: Optional template for generating prompts (None = use default).
    """

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


class ArtifactsPolicy(_FrozenPolicyModel):  # type: ignore[explicit-any]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library
    """Top-level artifacts.toml policy document.

    Attributes:
        artifacts: Map of artifact name -> artifact contract.
    """

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


class PolicyBundle(_FrozenPolicyModel):  # type: ignore[explicit-any]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library
    """Aggregate of all three policy documents.

    This is what the loader returns after validating all three TOML files together.
    """

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
        # Check both new role='analysis' and legacy embeds_analysis=True phases
        analysis_phases = {
            name: defn
            for name, defn in self.pipeline.phases.items()
            if defn.role == "analysis" or defn.embeds_analysis
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
