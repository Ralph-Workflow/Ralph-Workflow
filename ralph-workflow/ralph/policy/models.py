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

from pydantic import ConfigDict, Field, model_validator

from ralph.pydantic_compat import RalphBaseModel

# ---------------------------------------------------------------------------
# Shared types
# ---------------------------------------------------------------------------

# DrainName is a plain str — drain names are policy-declared and not constrained
# to a fixed built-in set. Any string value is valid; cross-validation ensures
# pipeline drains are bound in agents.agent_drains.
DrainName = str

PhaseRole = Literal[
    "execution",
    "analysis",
    "review",
    "commit",
    "verification",
    "terminal",
    "fanout_join",
]

# Role identifier constant — use this in non-model modules instead of the
# string literal "review" to avoid colliding with the default phase name 'review'.
ROLE_REVIEW: Literal["review"] = "review"


class _FrozenPolicyModel(RalphBaseModel):
    """Private base for frozen policy models.

    Owns ``model_config = ConfigDict(frozen=True)`` once so descendants do not
    repeat it. Pydantic v2 inherits ``model_config`` when descendants do not
    declare one of their own.
    """

    model_config = ConfigDict(frozen=True)


# ---------------------------------------------------------------------------
# agents.toml models
# ---------------------------------------------------------------------------


class AgentDrainConfig(_FrozenPolicyModel):
    """Binding from a named drain to an agent chain.

    Attributes:
        chain: Name of the agent chain to invoke when this drain is active.
        drain_class: Explicit capability class for this drain. Required when the drain name
            cannot be resolved by the SessionDrain enum. Must be one of:
            planning, development, analysis, review, fix, commit.
        capability_class: Optional MCP capability set override for this drain. When None,
            falls back to drain_class. Allows decoupling the workflow role classifier
            from the MCP capability surface (e.g., a 'planning' drain could use
            'development' capabilities by setting capability_class='development').
    """

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
        """Validate drain_class and capability_class against the allowed vocabulary when set."""
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


class AgentChainConfig(_FrozenPolicyModel):
    """Definition of a named agent fallback chain.

    Attributes:
        agents: Ordered list of agent names to try in sequence on failure.
        max_retries: Maximum retry attempts per agent before falling back.
        retry_delay_ms: Base delay between retries in milliseconds.
    """

    agents: list[str] = Field(..., min_length=1, description="Agents in fallback order")
    max_retries: int = Field(default=3, ge=0, description="Max retries per agent")
    retry_delay_ms: int = Field(default=1000, ge=0, description="Base retry delay in milliseconds")


class AgentsPolicy(_FrozenPolicyModel):
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
    agent_drains: dict[str, AgentDrainConfig] = Field(
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


class ArtifactHistoryPolicy(_FrozenPolicyModel):
    """Per-phase artifact history policy.

    When enabled, the runtime archives the previous canonical artifact and its
    Markdown handoff into a stable history location before overwriting them.
    The history is retained across re-planning loops so planning agents can
    inspect prior failed plans and analysis decisions.

    Attributes:
        enabled: Whether to keep history for this phase's output artifact.
        clear_on_fresh_entry: Whether a fresh (non-loopback) entry into this
            phase clears old history before prompt materialization.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    enabled: bool = Field(
        default=False,
        description="Whether to archive prior artifact versions before overwrite",
    )
    clear_on_fresh_entry: bool = Field(
        default=True,
        description="Whether a fresh phase entry clears old history (not a loopback)",
    )


class PhaseRetryPolicy(_FrozenPolicyModel):
    """Per-phase retry policy overriding chain-level defaults.

    Attributes:
        max_retries: Maximum retries for this phase.
        retry_delay_ms: Base retry delay in milliseconds.
        retry_in_session: Whether to preserve session on retry.
    """

    max_retries: int = Field(default=3, ge=0)
    retry_delay_ms: int = Field(default=1000, ge=0)
    retry_in_session: bool = False


class PhaseLoopPolicy(_FrozenPolicyModel):
    """Loop linkage for analysis phases.

    The analysis cap is declared once in ``pipeline.loop_counters``. This policy
    block only links the phase to its named counter and optionally declares the
    review outcome to stamp on loopback transitions.

    Attributes:
        iteration_state_field: Key in PipelineState.loop_iterations tracking this phase's counter.
        loopback_review_outcome: When set, loopback transitions set review_outcome to this value.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    iteration_state_field: str = Field(...)
    loopback_review_outcome: str | None = None


class PhaseDecisionRoute(_FrozenPolicyModel):
    """Route produced by an analysis decision.

    Attributes:
        target: Phase to route to when this decision is received.
        reset_loop: Whether to reset the analysis loop counter on this transition.
    """

    target: str = Field(...)
    reset_loop: bool = False


class PhaseCommitPolicy(_FrozenPolicyModel):
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


class PhaseVerificationPolicy(_FrozenPolicyModel):
    """Verification gating semantics for a phase.

    Attributes:
        kind: Kind of verification: 'artifact' checks for a required artifact;
            'none' skips gate validation.
        gate_for: What this verification gates (advancement, completion, release).
        on_failure_route: Phase to route to on verification failure (None = fail pipeline).
    """

    kind: Literal["artifact", "none"]
    gate_for: Literal["advancement", "completion", "release"]
    on_failure_route: str | None = None


class LoopCounterConfig(_FrozenPolicyModel):
    """Declaration of a named loop iteration counter in the pipeline.

    Loop counters track how many times an analysis phase has looped back.
    They are keyed by the value used in PhaseLoopPolicy.iteration_state_field.

    Attributes:
        default_max: Default maximum iterations (overridable via config).
        description: Human-readable description of this counter's purpose.
    """

    default_max: int = Field(default=3, ge=0, description="Default maximum iterations")
    description: str = Field(default="", description="Human-readable description")


class BudgetCounterConfig(_FrozenPolicyModel):
    """Declaration of a named budget counter in the pipeline.

    Budget counters track outer-progress (completed cycles) and remaining budget.
    They are keyed by the value used in PhaseCommitPolicy.increments_counter.

    Attributes:
        description: Human-readable description of this counter's purpose.
        tracks_budget: Whether remaining budget is tracked (True = exhaustion matters).
        default_max: Default maximum budget when no CLI override is supplied.
            Required — must be declared explicitly in pipeline.toml so the
            runtime never invents a hidden cap. Use --counter NAME=VALUE to override.
    """

    description: str = Field(default="", description="Human-readable description")
    tracks_budget: bool = Field(
        default=True,
        description="Whether remaining budget is tracked for post-commit routing",
    )
    default_max: int = Field(
        ...,
        ge=0,
        description="Default maximum budget — required so the runtime never invents a hidden cap",
    )


class RecoveryPolicy(_FrozenPolicyModel):
    """Pipeline-wide recovery policy.

    Attributes:
        cycle_cap: Maximum full-chain exhaustion cycles before exit.
        failed_route: Phase to route to on terminal pipeline failure.
            Must reference a declared phase with role='terminal' and
            terminal_outcome='failure'. 'phase_failed', 'exit_failure', and
            'failed' are no longer accepted.
        terminal_failure_phase: Optional name of the declared terminal failure phase
            (must have role='terminal' and terminal_outcome='failure' in pipeline.phases).
            When set, enables BFS reachability validation for failure paths.
        preserve_session_on_categories: Failure categories that preserve agent session.
    """

    cycle_cap: int = Field(default=200, ge=1)
    failed_route: str = Field(
        default="failed_terminal",
        description=(
            "Phase to route to on terminal pipeline failure. "
            "Must reference a declared phase with role='terminal' and terminal_outcome='failure'. "
            "'phase_failed', 'exit_failure', and 'failed' are no longer accepted. "
            "Example: declare [phases.failed_terminal] and set failed_route='failed_terminal'."
        ),
    )
    terminal_failure_phase: str | None = Field(
        default=None,
        description=(
            "Optional name of the declared terminal failure phase "
            "(must have role='terminal' and terminal_outcome='failure' in pipeline.phases). "
            "When set, failure routing references this policy-declared phase."
        ),
    )
    preserve_session_on_categories: tuple[str, ...] = ("agent",)

    @model_validator(mode="before")
    @classmethod
    def _reject_legacy_route_fields(cls, data: object) -> object:
        """Reject removed/deprecated recovery fields with actionable errors."""
        if not isinstance(data, dict):
            return data
        d = cast("dict[str, object]", dict(data))
        if "terminal_recovery_route" in d:
            raise ValueError(
                "recovery.terminal_recovery_route is deprecated; rename it to "
                "recovery.failed_route. See docs/migration/policy-v2.md."
            )
        failed_route = d.get("failed_route")
        if failed_route in ("phase_failed", "exit_failure"):
            raise ValueError(
                f"recovery.failed_route: '{failed_route}' is no longer supported. "
                "Declare a terminal failure phase with role='terminal' and "
                "terminal_outcome='failure' and reference it via recovery.failed_route "
                "(and optionally recovery.terminal_failure_phase). "
                "See docs/sphinx/policy-driven-overhaul-migration.md."
            )
        if failed_route == "failed":
            raise ValueError(
                "recovery.failed_route: 'failed' is no longer accepted as a pseudo-phase alias. "
                "Declare a phase with role='terminal' and terminal_outcome='failure' "
                "and reference it via recovery.failed_route. "
                "Example: add [phases.failed_terminal] with role='terminal' and "
                "terminal_outcome='failure', then set failed_route='failed_terminal'. "
                "See docs/sphinx/policy-driven-overhaul-migration.md."
            )
        return d


# ---------------------------------------------------------------------------
# pipeline.toml models
# ---------------------------------------------------------------------------


class PhaseTransition(_FrozenPolicyModel):
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


class PhaseParallelization(_FrozenPolicyModel):
    """Transition-scoped parallelization policy for a pipeline phase.

    When present on a PhaseDefinition, same-workspace fan-out is enabled for
    that phase. When absent (None), multi-work-unit plans are rejected before
    execution — no silent serialization, no inferred fan-out.

    Attributes:
        mode: Parallelization mode. Only 'same_workspace' is supported in v1.
        max_parallel_workers: Maximum number of concurrent work units.
        max_work_units: Upper bound on total work units in a planning artifact.
        require_allowed_directories: Require each work unit to declare allowed_directories.
        post_fanout_verification: When True, run serialized workspace-wide verification
            after all workers complete.
    """

    mode: Literal["same_workspace"] = Field(
        default="same_workspace",
        description="Parallelization mode; only 'same_workspace' is supported in v1",
    )
    max_parallel_workers: int = Field(
        default=8,
        ge=1,
        description="Maximum allowed concurrent work units",
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


class PhaseWorkflowFallback(_FrozenPolicyModel):
    """Policy-declared workflow-level fallback when a phase's agent chain is exhausted.

    When the agent chain for a phase is fully exhausted, the reducer checks this
    field first. If set, it routes to `target` instead of the global failure route.
    This allows per-phase fallback behavior to be expressed in policy.

    Attributes:
        target: Phase to route to when the agent chain at this phase is exhausted.
        note: Optional rationale, surfaced by --explain-policy.
    """

    target: str = Field(..., description="Phase to route to when the agent chain is exhausted")
    note: str | None = Field(
        default=None,
        description="Optional rationale for this fallback, shown by --explain-policy",
    )


class PhaseDefinition(_FrozenPolicyModel):
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
        artifact_required: Whether this phase's output artifact is required for phase success
            when the phase's drain has an artifact contract. Defaults to True.
        terminal_outcome: Explicit terminal outcome; required when role='terminal'.
        bypass_routes: Named bypass routes (e.g. clean -> review_commit).
        clean_outcome: For role='review': the bypass_routes key that means the review
            is clean (no issues). The reducer looks up this key in bypass_routes to
            find the target phase for a clean review. Required when bypass_routes is
            non-empty and role='review'.
        issues_outcome: For role='review': the value to set as review_outcome when
            issues are found. Required when role='review'. Drives review_outcome
            propagation so the pipeline knows what kind of issues were flagged.
        prompt_template: File-backed .jinja prompt template for this phase.
        continuation_template: Optional continuation .jinja prompt template.
        loopback_prompt_template: Optional alternate .jinja prompt template to use
            when the phase is re-entered from an analysis loopback with structured
            feedback to incorporate.
        parallelization: Optional transition-scoped parallelization policy. When None,
            multi-work-unit plans must not fan out from this phase.
        workflow_fallback: Optional policy-declared fallback when this phase's agent chain
            is exhausted. When set, routes to target instead of the global failure route.
    """

    drain: str = Field(..., description="Drain binding for this phase")
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
    artifact_required: bool = Field(
        default=True,
        description=(
            "Whether this phase's output artifact is required for phase success when "
            "the phase's drain has an artifact contract. Defaults to True."
        ),
    )
    terminal_outcome: Literal["success", "failure"] | None = Field(
        default=None,
        description="Explicit terminal outcome declaration",
    )
    bypass_routes: dict[str, str] = Field(
        default_factory=dict,
        description="Named bypass routes (outcome -> target phase)",
    )
    clean_outcome: str | None = Field(
        default=None,
        description=(
            "For role='review': the bypass_routes key that signals a clean review. "
            "The reducer looks up this key in bypass_routes to find the target phase. "
            "Required when role='review' and bypass_routes is non-empty."
        ),
    )
    issues_outcome: str | None = Field(
        default=None,
        description=(
            "For role='review': the value to set as review_outcome when issues are found. "
            "Required when role='review'. Drives review_outcome propagation downstream."
        ),
    )

    prompt_template: str | None = Field(
        default=None,
        description="File-backed .jinja prompt template for this phase",
    )
    continuation_template: str | None = Field(
        default=None,
        description="Optional continuation .jinja prompt template for this phase",
    )
    loopback_prompt_template: str | None = Field(
        default=None,
        description=(
            "Optional alternate .jinja prompt template for loopback retries that "
            "need structured upstream feedback."
        ),
    )
    parallelization: PhaseParallelization | None = Field(
        default=None,
        description=(
            "Transition-scoped parallelization policy. When None, multi-work-unit plans "
            "must not fan out from this phase."
        ),
    )
    artifact_history: ArtifactHistoryPolicy | None = Field(
        default=None,
        description=(
            "Optional artifact history policy. When set with enabled=True, the runtime "
            "archives the prior canonical artifact and Markdown handoff before overwrite. "
            "Phases sharing the same drain must agree on artifact_history.enabled."
        ),
    )
    workflow_fallback: PhaseWorkflowFallback | None = Field(
        default=None,
        description=(
            "Policy-declared fallback route when this phase's agent chain is exhausted. "
            "When set, routes to target instead of the global recovery.failed_route. "
            "Takes precedence over recovery.failed_route on chain exhaustion."
        ),
    )

    @model_validator(mode="before")
    @classmethod
    def _reject_legacy_fields(cls, data: object) -> object:
        """Reject removed legacy fields with actionable errors."""
        if not isinstance(data, dict):
            return data
        d: dict[str, object] = cast("dict[str, object]", data)
        if d.get("embeds_analysis"):
            raise ValueError(
                "PhaseDefinition.embeds_analysis has been removed. "
                "Set role='analysis' instead. "
                "See docs/sphinx/policy-driven-overhaul-migration.md."
            )
        if d.get("requires_commit"):
            raise ValueError(
                "PhaseDefinition.requires_commit has been removed. "
                "Set role='commit' instead. "
                "See docs/sphinx/policy-driven-overhaul-migration.md."
            )
        return d


class PostCommitRouteWhen(_FrozenPolicyModel):
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


class PostCommitRoute(_FrozenPolicyModel):
    """Budget-guarded route applied after commit success."""

    when: PostCommitRouteWhen = Field(..., description="Route condition")
    target: str = Field(..., description="Target phase when condition matches")


def _terminal_phase_names(policy: PipelinePolicy) -> set[str]:
    """Return all terminal phase names from policy.

    Includes:
    - policy.terminal_phase (the declared success terminal)
    - policy.recovery.failed_route (the failure route or declared phase)
    - Any phase with role='terminal'
    """
    names: set[str] = {
        policy.terminal_phase,
        policy.recovery.failed_route,
    }
    names.update(name for name, defn in policy.phases.items() if defn.role == "terminal")
    return names


class PipelinePolicy(_FrozenPolicyModel):
    """Top-level pipeline.toml policy document.

    Attributes:
        phases: Map of phase name -> phase definition.
        entry_phase: Name of the phase where the pipeline starts.
        terminal_phase: Name of the phase that marks successful pipeline completion.
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
    default_phase_retry_policy: PhaseRetryPolicy = Field(
        default_factory=PhaseRetryPolicy,
        description="Default retry policy for phases without explicit retry_policy",
    )
    recovery: RecoveryPolicy = Field(
        default_factory=RecoveryPolicy,
        description="Pipeline-wide recovery configuration",
    )

    @model_validator(mode="before")
    @classmethod
    def no_legacy_parallel_execution_block(cls, values: object) -> object:
        """Reject configs that still use the removed global [parallel_execution] block."""
        if isinstance(values, dict) and "parallel_execution" in values:
            raise ValueError(
                "The global [parallel_execution] block has been removed. "
                "Move max_parallel_workers, max_work_units, require_allowed_directories, "
                "and post_fanout_verification under [phases.<phase>.parallelization] "
                "(typically [phases.development.parallelization]). "
                "Run `ralph --regenerate-config` to refresh the bundled template if this "
                "file came from an older bootstrap. See docs/migration/parallel-mode.md."
            )
        return values

    def terminal_states(self) -> set[str]:
        """Return the full set of terminal state names for transition validation."""
        return _terminal_phase_names(self)

    @model_validator(mode="after")
    def all_transitions_reference_known_phases(self) -> PipelinePolicy:
        """Ensure every transition target is a defined phase or terminal."""
        ts = self.terminal_states()
        for phase_name, phase_def in self.phases.items():
            t = phase_def.transitions
            for label, target in [
                ("on_success", t.on_success),
                ("on_failure", t.on_failure),
                ("on_loopback", t.on_loopback),
            ]:
                if (
                    target is not None
                    and target not in ts
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
        terminal = self.terminal_states()
        for name, phase_def in self.phases.items():
            if name in terminal:
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
        ts = self.terminal_states()
        for route in self.post_commit_routes:
            if route.target not in ts and route.target not in self.phases:
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
    def parallelization_targets_non_terminal_phases(self) -> PipelinePolicy:
        """Ensure only non-terminal phases declare a parallelization policy."""
        for phase_name, phase_def in self.phases.items():
            if phase_def.parallelization is not None and phase_def.role == "terminal":
                raise ValueError(
                    f"Phase '{phase_name}' declares parallelization but has terminal role"
                )
        return self

    @model_validator(mode="after")
    def decision_routes_target_known_phases(self) -> PipelinePolicy:
        """Ensure every PhaseDecisionRoute.target resolves to a known phase or terminal."""
        ts = self.terminal_states()
        for phase_name, phase_def in self.phases.items():
            for decision_name, route in phase_def.decisions.items():
                if route.target not in ts and route.target not in self.phases:
                    raise ValueError(
                        f"Phase '{phase_name}' decisions['{decision_name}'] targets "
                        f"unknown phase '{route.target}'"
                    )
        return self

    @model_validator(mode="after")
    def bypass_routes_target_known_phases(self) -> PipelinePolicy:
        """Ensure every bypass_route target resolves to a known phase or terminal."""
        ts = self.terminal_states()
        for phase_name, phase_def in self.phases.items():
            for outcome, target in phase_def.bypass_routes.items():
                if target not in ts and target not in self.phases:
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

    @model_validator(mode="after")
    def workflow_fallback_targets_valid(self) -> PipelinePolicy:
        """Ensure workflow_fallback.target references a known phase or terminal."""
        ts = self.terminal_states()
        for phase_name, phase_def in self.phases.items():
            if phase_def.workflow_fallback is not None:
                target = phase_def.workflow_fallback.target
                if target not in ts and target not in self.phases:
                    raise ValueError(
                        f"Phase '{phase_name}' workflow_fallback.target "
                        f"'{target}' is not a known phase or terminal"
                    )
        return self

    @model_validator(mode="after")
    def terminal_failure_phase_valid(self) -> PipelinePolicy:
        """Ensure terminal_failure_phase references a declared phase with correct role/outcome."""
        tfp = self.recovery.terminal_failure_phase
        if tfp is None:
            return self
        if tfp not in self.phases:
            raise ValueError(
                f"recovery.terminal_failure_phase: '{tfp}' is not a known phase. "
                f"Declared phases: {sorted(self.phases.keys())}"
            )
        phase_def = self.phases[tfp]
        if phase_def.role != "terminal" or phase_def.terminal_outcome != "failure":
            raise ValueError(
                f"recovery.terminal_failure_phase: '{tfp}' must have "
                f"role='terminal' and terminal_outcome='failure'"
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


class ArtifactContract(_FrozenPolicyModel):
    """Contract for an artifact type submitted by an agent at a given drain.

    Attributes:
        drain: Which drain this artifact is submitted at.
        artifact_type: Type identifier for the artifact (e.g., planning_json).
        decision_vocabulary: Valid values for the decision field (for analysis drains).
        prompt_template: Optional template for generating prompts (None = use default).
        artifact_json_path: Override path for the artifact JSON file. When set,
            overrides the default '.agent/artifacts/<artifact_type>.json' path.
        markdown_summary_path: Path to the human-readable markdown summary for this
            artifact. When set, the runner renders a markdown handoff at this path.
    """

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
    artifact_json_path: str | None = Field(
        default=None,
        description=(
            "Override path for the artifact JSON file. "
            "When None, falls back to '.agent/artifacts/<artifact_type>.json'."
        ),
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
        """Reject fields that belong to pipeline.toml rather than artifacts.toml."""
        if not isinstance(data, dict):
            return data
        raw = cast("dict[str, object]", dict(data))
        if "artifact_required" in raw:
            raise ValueError(
                "ArtifactContract.artifact_required has moved to pipeline.toml. "
                "Set phases.<phase>.artifact_required instead."
            )
        return raw


class ArtifactsPolicy(_FrozenPolicyModel):
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


# ---------------------------------------------------------------------------
# Convenience aggregate type
# ---------------------------------------------------------------------------


class PolicyBundle(_FrozenPolicyModel):
    """Aggregate of all three policy documents.

    This is what the loader returns after validating all three TOML files together.
    """

    agents: AgentsPolicy = Field(..., description="Agent chains and drain bindings")
    pipeline: PipelinePolicy = Field(..., description="Phase graph and routing")
    artifacts: ArtifactsPolicy = Field(..., description="Artifact contracts per drain")

    @model_validator(mode="after")
    def all_pipeline_drains_are_bound(self) -> PolicyBundle:
        """Ensure every drain used in pipeline.phases is bound in agents.agent_drains.

        Skips all terminal-role phases since they never invoke agents.
        """
        unbound: list[str] = []
        for phase_name, phase_def in self.pipeline.phases.items():
            # Skip terminal phases — they never invoke agents
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
    def analysis_decision_vocabulary_present(self) -> PolicyBundle:
        """Ensure analysis phases have decision_vocabulary defined."""
        analysis_phases = {
            name: defn
            for name, defn in self.pipeline.phases.items()
            if defn.role == "analysis"
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
