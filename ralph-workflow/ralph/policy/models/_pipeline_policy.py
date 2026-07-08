"""PipelinePolicy Pydantic model."""

from typing import Self

from pydantic import Field, model_validator

from ralph.policy.models._budget_counter_config import BudgetCounterConfig
from ralph.policy.models._frozen_policy_model import _FrozenPolicyModel
from ralph.policy.models._lifecycle_phase_policy import LifecyclePhasePolicy
from ralph.policy.models._loop_counter_config import LoopCounterConfig
from ralph.policy.models._phase_definition import PhaseDefinition
from ralph.policy.models._phase_retry_policy import PhaseRetryPolicy
from ralph.policy.models._policy_block import PolicyBlock
from ralph.policy.models._post_commit_route import PostCommitRoute
from ralph.policy.models._recovery_policy import RecoveryPolicy


class PipelinePolicy(_FrozenPolicyModel):
    """Top-level pipeline.toml policy document."""

    blocks: dict[str, PolicyBlock] = Field(
        default_factory=dict,
        description="Authoring-time block graph for block-oriented pipeline policies.",
    )
    phases: dict[str, PhaseDefinition] = Field(
        default_factory=dict,
        description="All compiled runtime phases in the pipeline graph",
    )
    entry_block: str | None = Field(
        default=None,
        description="Authoring-time block where pipeline begins.",
    )
    entry_phase: str = Field(
        default="planning",
        description="Compiled phase where pipeline begins",
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
    lifecycle_phases: dict[str, LifecyclePhasePolicy] = Field(
        default_factory=dict,
        description="Lifecycle completion metadata keyed by compiled phase name.",
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
        if isinstance(values, dict) and "parallel_execution" in values:
            raise ValueError(
                "The global [parallel_execution] block has been removed. "
                "Move max_parallel_workers, max_work_units, require_allowed_directories, "
                "and post_fanout_verification under [phases.<phase>.parallelization] "
                "(typically [phases.development.parallelization]). "
                "Run `ralph --regenerate-config` to refresh the bundled template if "
                "this file came from an older bootstrap. See "
                "docs/sphinx/advanced-pipeline-configuration.md"
                "#parallel-execution-agent-driven."
            )
        return values

    def terminal_states(self) -> set[str]:
        """Return the full set of terminal state names for transition validation."""
        return _terminal_phase_names(self)

    @model_validator(mode="after")
    def all_transitions_reference_known_phases(self) -> Self:
        ts = self.terminal_states()
        for phase_name, phase_def in self.phases.items():
            t = phase_def.transitions
            for label, target in [
                ("on_success", t.on_success),
                ("on_failure", t.on_failure),
                ("on_loopback", t.on_loopback),
            ]:
                if target is not None and target not in ts and target not in self.phases:
                    raise ValueError(
                        f"Phase '{phase_name}' transitions.{label} references "
                        f"unknown phase '{target}'"
                    )
        return self

    @model_validator(mode="after")
    def entry_phase_exists(self) -> Self:
        if self.entry_phase not in self.phases:
            raise ValueError(f"entry_phase '{self.entry_phase}' is not defined in phases")
        return self

    @model_validator(mode="after")
    def no_phase_cycles_without_loopback(self) -> Self:
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
    def post_commit_routes_reference_known_targets(self) -> Self:
        ts = self.terminal_states()
        for route in self.post_commit_routes:
            if route.target not in ts and route.target not in self.phases:
                raise ValueError(f"post_commit_routes target '{route.target}' is not a known phase")
        return self

    @model_validator(mode="after")
    def post_commit_routes_unique_conditions(self) -> Self:
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
    def parallelization_targets_non_terminal_phases(self) -> Self:
        for phase_name, phase_def in self.phases.items():
            if phase_def.parallelization is not None and phase_def.role == "terminal":
                raise ValueError(
                    f"Phase '{phase_name}' declares parallelization but has terminal role"
                )
        return self

    @model_validator(mode="after")
    def decision_routes_target_known_phases(self) -> Self:
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
    def bypass_routes_target_known_phases(self) -> Self:
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
    def loop_counter_references_valid(self) -> Self:
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
    def budget_counter_references_valid(self) -> Self:
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
    def workflow_fallback_targets_valid(self) -> Self:
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
    def terminal_failure_phase_valid(self) -> Self:
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


def _terminal_phase_names(policy: PipelinePolicy) -> set[str]:
    """Return all terminal phase names from policy."""
    names: set[str] = {
        policy.terminal_phase,
        policy.recovery.failed_route,
    }
    names.update(name for name, defn in policy.phases.items() if defn.role == "terminal")
    return names
