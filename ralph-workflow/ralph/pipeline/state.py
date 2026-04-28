"""Immutable pipeline state model.

This module defines PipelineState - the single source of truth for pipeline
execution progress. It serves dual purposes:
1. Runtime State: Tracks current phase, iteration counters, agent chain state
2. Checkpoint Payload: Serializes to JSON for resume functionality

PipelineState is IMMUTABLE from the reducer's perspective. State transitions
occur exclusively through the reduce function.

POLICY-DRIVEN STATE TRACKING
==============================
Loop counters (loop_iterations / loop_caps) and phase chains (phase_chains)
are keyed by policy-declared names, not hardcoded field names. This enables
custom workflows with arbitrary phase and counter names to work without
modifying source code.

Budget counters (budget_remaining / outer_progress) track remaining budget
and completed cycles for each policy-declared budget counter.

Legacy checkpoint fields are migrated to the generic dicts at deserialise time
via the _migrate_legacy_state_fields model_validator.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ralph.config.enums import (
    PHASE_COMPLETE,
    PipelinePhase,
)
from ralph.pipeline._chain_migration import LEGACY_CHAIN_FIELD_TO_PHASE
from ralph.pipeline.work_units import WorkUnit  # noqa: TC001
from ralph.pipeline.worker_state import WorkerState  # noqa: TC001

if TYPE_CHECKING:
    from ralph.policy.models import DrainName

# Legacy loop iteration field names → their corresponding max-cap field names.
_LEGACY_LOOP_FIELDS: dict[str, str] = {
    "development_analysis_iteration": "max_development_analysis_iterations",
    "review_analysis_iteration": "max_review_analysis_iterations",
}

# Legacy budget remaining fields → counter name mappings for migration.
_LEGACY_BUDGET_REMAINING_MAP: dict[str, str] = {
    "development_budget_remaining": "iteration",
    "review_budget_remaining": "reviewer_pass",
}

# Legacy outer-progress fields → counter name mappings for migration.
_LEGACY_OUTER_PROGRESS_MAP: dict[str, str] = {
    "iteration": "iteration",
    "reviewer_pass": "reviewer_pass",
}


class _FrozenPipelineStateModel(BaseModel):  # type: ignore[explicit-any]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library
    """Private base for frozen pipeline state models."""

    model_config = ConfigDict(frozen=True)


class AgentChainState(_FrozenPipelineStateModel):  # type: ignore[explicit-any]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library
    """State for agent fallback chain management.

    Attributes:
        agents: List of agent names in the fallback chain.
        current_index: Current agent index being used.
        retries: Number of retries for current agent.
    """

    agents: list[str] = Field(default_factory=list)
    current_index: int = 0
    retries: int = 0

    def with_retry_increment(self) -> AgentChainState:
        """Return a copy with retries incremented by 1."""
        return AgentChainState(
            agents=self.agents,
            current_index=self.current_index,
            retries=self.retries + 1,
        )

    def with_advance(self) -> AgentChainState:
        """Return a copy advanced to the next agent with retries reset to 0."""
        return AgentChainState(
            agents=self.agents,
            current_index=self.current_index + 1,
            retries=0,
        )


class RebaseState(_FrozenPipelineStateModel):  # type: ignore[explicit-any]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library
    """State for git rebase operations."""

    pending: bool = False
    in_progress: bool = False
    completed: bool = False


class CommitState(_FrozenPipelineStateModel):  # type: ignore[explicit-any]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library
    """State for commit operations."""

    message_prepared: bool = False
    diff_prepared: bool = False
    agent_invoked: bool = False


class RunMetrics(_FrozenPipelineStateModel):  # type: ignore[explicit-any]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library
    """Run-level execution metrics."""

    total_agent_calls: int = 0
    total_continuations: int = 0
    total_fallbacks: int = 0
    total_retries: int = 0

    def with_retry_increment(self) -> RunMetrics:
        """Return a copy with total_retries incremented by 1."""
        return RunMetrics(
            total_agent_calls=self.total_agent_calls,
            total_continuations=self.total_continuations,
            total_fallbacks=self.total_fallbacks,
            total_retries=self.total_retries + 1,
        )

    def with_fallback_increment(self) -> RunMetrics:
        """Return a copy with total_fallbacks incremented by 1."""
        return RunMetrics(
            total_agent_calls=self.total_agent_calls,
            total_continuations=self.total_continuations,
            total_fallbacks=self.total_fallbacks + 1,
            total_retries=self.total_retries,
        )

    def with_continuation_increment(self) -> RunMetrics:
        """Return a copy with total_continuations incremented by 1."""
        return RunMetrics(
            total_agent_calls=self.total_agent_calls,
            total_continuations=self.total_continuations + 1,
            total_fallbacks=self.total_fallbacks,
            total_retries=self.total_retries,
        )


class FalloverRecord(_FrozenPipelineStateModel):  # type: ignore[explicit-any]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library
    """A record of a single agent fallover event persisted in pipeline state."""

    phase: str
    from_agent: str
    to_agent: str
    timestamp_iso: str


class PipelineState(_FrozenPipelineStateModel):  # type: ignore[explicit-any]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library
    """Immutable snapshot of pipeline execution state.

    This is the checkpoint payload - the single source of truth for pipeline progress.
    Serialize it to JSON to save state; deserialize to resume interrupted runs.

    GENERIC TRACKING FIELDS (policy-keyed):
        phase_chains: Per-phase agent chain state keyed by canonical phase name.
        loop_iterations: Loop iteration counters keyed by iteration_state_field name.
        loop_caps: Loop iteration caps keyed by iteration_state_field name.
        budget_remaining: Remaining budget keyed by budget counter name.
        outer_progress: Completed cycle counts keyed by budget counter name.

    LEGACY FIELDS (backward-compat aliases, populated from policy at startup):
        iteration: Alias for outer_progress['iteration'] (dev cycles completed).
        total_iterations: Total allowed development iterations.
        reviewer_pass: Alias for outer_progress['reviewer_pass'] (review passes).
        total_reviewer_passes: Total allowed review passes.
        development_budget_remaining: Alias for budget_remaining['iteration'].
        review_budget_remaining: Alias for budget_remaining['reviewer_pass'].
        review_issues_found: Whether review found issues (derived from review_outcome).
        development_analysis_iteration: Alias for loop_iterations['development_analysis_iteration'].
        max_development_analysis_iterations: Alias for loop_caps['development_analysis_iteration'].
        review_analysis_iteration: Alias for loop_iterations['review_analysis_iteration'].
        max_review_analysis_iterations: Alias for loop_caps['review_analysis_iteration'].
    """

    phase: PipelinePhase = "planning"
    previous_phase: PipelinePhase | None = None

    # Legacy budget fields — kept as real fields for display/checkpoint compat.
    # They are also mirrored in budget_remaining / outer_progress at startup.
    iteration: int = 0
    total_iterations: int = 5
    reviewer_pass: int = 0
    total_reviewer_passes: int = 2
    development_budget_remaining: int = 0
    review_budget_remaining: int = 0

    # Review outcome tracking (replaces direct review_issues_found writes)
    review_outcome: str | None = None

    # Generic per-phase chain state (keyed by canonical phase name from policy)
    phase_chains: dict[str, AgentChainState] = Field(default_factory=dict)

    # Generic loop iteration tracking (keyed by iteration_state_field from loop_policy)
    loop_iterations: dict[str, int] = Field(default_factory=dict)
    loop_caps: dict[str, int] = Field(default_factory=dict)

    # Generic budget counter tracking (keyed by budget counter name from budget_counters)
    budget_remaining: dict[str, int] = Field(default_factory=dict)
    outer_progress: dict[str, int] = Field(default_factory=dict)

    # Legacy analysis iteration fields — kept for backward compat with display/checkpoint.
    # These are mirrored from loop_iterations at startup.
    development_analysis_iteration: int = Field(default=0, ge=0)
    max_development_analysis_iterations: int = Field(default=3, ge=0)
    review_analysis_iteration: int = Field(default=0, ge=0)
    max_review_analysis_iterations: int = Field(default=2, ge=0)

    rebase: RebaseState = Field(default_factory=RebaseState)
    commit: CommitState = Field(default_factory=CommitState)
    metrics: RunMetrics = Field(default_factory=RunMetrics)
    checkpoint_saved_count: int = 0
    recovery_epoch: int = 0
    interrupted_by_user: bool = False
    git_auth_configured: bool = False
    pr_created: bool = False
    pr_url: str | None = None
    push_count: int = 0
    last_error: str | None = None
    last_reviewed_sha: str | None = None

    # Policy-derived fields (set at startup and after phase transitions)
    policy_entry_phase: PipelinePhase = "planning"
    current_drain: str | None = None

    work_units: tuple[WorkUnit, ...] = Field(default_factory=tuple)
    worker_states: dict[str, WorkerState] = Field(default_factory=dict)

    # Recovery observability fields — all have defaults so legacy checkpoints load cleanly
    recovery_cycle_count: int = 0
    fallover_history: tuple[FalloverRecord, ...] = Field(default_factory=tuple)
    last_failure_category: str | None = None
    last_connectivity_state: str = "unknown"
    recovery_cycle_cap: int = Field(default=200, ge=1)
    last_retry_delay_ms: int = 0
    last_agent_session_id: str | None = None
    session_preserve_retry_pending: bool = False

    @model_validator(mode="before")
    @classmethod
    def _migrate_legacy_state_fields(cls, data: object) -> object:
        """Migrate legacy checkpoint fields into generic dicts.

        Handles old checkpoints that stored:
        - Typed chain fields → migrated into phase_chains
        - Legacy loop iteration fields → migrated into loop_iterations / loop_caps
        - Legacy budget fields → migrated into budget_remaining / outer_progress
        """
        if not isinstance(data, dict):
            return data
        d = cast("dict[str, object]", dict(data))

        # Migrate typed chain fields into phase_chains
        _raw = d.get("phase_chains")
        raw_chains = cast("dict[str, object]", _raw) if _raw is not None else {}
        phase_chains: dict[str, object] = dict(raw_chains)
        for old_field, phase_key in LEGACY_CHAIN_FIELD_TO_PHASE.items():
            if old_field in d:
                val = d.pop(old_field)
                if val is not None and phase_key not in phase_chains:
                    phase_chains[phase_key] = val
        d["phase_chains"] = phase_chains

        # Migrate legacy loop iteration fields into loop_iterations and loop_caps
        _raw_li = d.get("loop_iterations")
        loop_iterations: dict[str, object] = dict(
            cast("dict[str, object]", _raw_li) if _raw_li is not None else {}
        )
        _raw_lc = d.get("loop_caps")
        loop_caps: dict[str, object] = dict(
            cast("dict[str, object]", _raw_lc) if _raw_lc is not None else {}
        )
        for iter_field, cap_field in _LEGACY_LOOP_FIELDS.items():
            if iter_field not in loop_iterations and iter_field in d:
                loop_iterations[iter_field] = d[iter_field]
            if iter_field not in loop_caps and cap_field in d:
                loop_caps[iter_field] = d[cap_field]
        d["loop_iterations"] = loop_iterations
        d["loop_caps"] = loop_caps

        # Migrate legacy budget fields into budget_remaining and outer_progress
        _raw_br = d.get("budget_remaining")
        budget_remaining: dict[str, object] = dict(
            cast("dict[str, object]", _raw_br) if _raw_br is not None else {}
        )
        _raw_op = d.get("outer_progress")
        outer_progress_data: dict[str, object] = dict(
            cast("dict[str, object]", _raw_op) if _raw_op is not None else {}
        )
        for legacy_field, counter_name in _LEGACY_BUDGET_REMAINING_MAP.items():
            if counter_name not in budget_remaining and legacy_field in d:
                budget_remaining[counter_name] = d[legacy_field]
        for legacy_field, counter_name in _LEGACY_OUTER_PROGRESS_MAP.items():
            if counter_name not in outer_progress_data and legacy_field in d:
                outer_progress_data[counter_name] = d[legacy_field]
        d["budget_remaining"] = budget_remaining
        d["outer_progress"] = outer_progress_data

        return d

    @field_validator("work_units", mode="before")
    @classmethod
    def _coerce_work_units(cls, v: object) -> tuple[WorkUnit, ...]:
        if v is None:
            return ()
        if isinstance(v, list):
            return tuple(v)
        if isinstance(v, tuple):
            return v
        raise TypeError(f"Expected list or tuple for work_units, got {type(v).__name__!r}")

    @field_validator("worker_states", mode="before")
    @classmethod
    def _coerce_worker_states(cls, v: object) -> dict[str, WorkerState]:
        if v is None:
            return {}
        if isinstance(v, dict):
            return v
        raise TypeError(f"Expected dict for worker_states, got {type(v).__name__!r}")

    @field_validator("fallover_history", mode="before")
    @classmethod
    def _coerce_fallover_history(cls, v: object) -> tuple[FalloverRecord, ...]:
        if v is None:
            return ()
        if isinstance(v, list):
            return tuple(
                FalloverRecord.model_validate(item) if isinstance(item, dict) else item
                for item in v
            )
        if isinstance(v, tuple):
            return v
        raise TypeError(f"Expected list or tuple for fallover_history, got {type(v).__name__!r}")

    @field_validator("phase_chains", mode="before")
    @classmethod
    def _coerce_phase_chains(cls, v: object) -> dict[str, AgentChainState]:
        if v is None:
            return {}
        if isinstance(v, dict):
            return {
                str(key): AgentChainState.model_validate(value)
                if isinstance(value, dict)
                else cast("AgentChainState", value)
                for key, value in v.items()
            }
        raise TypeError(f"Expected dict for phase_chains, got {type(v).__name__!r}")

    @field_validator("loop_iterations", mode="before")
    @classmethod
    def _coerce_loop_iterations(cls, v: object) -> dict[str, int]:
        if v is None:
            return {}
        if isinstance(v, dict):
            return {str(k): int(val) for k, val in v.items()}
        raise TypeError(f"Expected dict for loop_iterations, got {type(v).__name__!r}")

    @field_validator("loop_caps", mode="before")
    @classmethod
    def _coerce_loop_caps(cls, v: object) -> dict[str, int]:
        if v is None:
            return {}
        if isinstance(v, dict):
            return {str(k): int(val) for k, val in v.items()}
        raise TypeError(f"Expected dict for loop_caps, got {type(v).__name__!r}")

    @field_validator("budget_remaining", mode="before")
    @classmethod
    def _coerce_budget_remaining(cls, v: object) -> dict[str, int]:
        if v is None:
            return {}
        if isinstance(v, dict):
            return {str(k): int(val) for k, val in v.items()}
        raise TypeError(f"Expected dict for budget_remaining, got {type(v).__name__!r}")

    @field_validator("outer_progress", mode="before")
    @classmethod
    def _coerce_outer_progress(cls, v: object) -> dict[str, int]:
        if v is None:
            return {}
        if isinstance(v, dict):
            return {str(k): int(val) for k, val in v.items()}
        raise TypeError(f"Expected dict for outer_progress, got {type(v).__name__!r}")

    @classmethod
    def known_loop_iteration_fields(cls) -> frozenset[str]:
        """Return the set of known loop iteration state field names.

        This is no longer a hardcoded set — it returns the legacy field names
        that are always supported. Custom fields declared in policy loop_counters
        are also valid; they are validated against policy at load time.
        """
        return frozenset(_LEGACY_LOOP_FIELDS.keys())

    @property
    def review_issues_found(self) -> bool:
        """Backward-compat derived property: True when review_outcome indicates issues."""
        return self.review_outcome is not None and self.review_outcome != "clean"

    def is_complete(self) -> bool:
        """Check if pipeline has reached a terminal success state."""
        return self.phase == PHASE_COMPLETE

    def current_agent(self) -> str | None:
        """Get the current agent for the active phase."""
        chain = self.chain_for_phase(self.phase)
        if chain is None:
            return None
        if not chain.agents or chain.current_index >= len(chain.agents):
            return None
        return chain.agents[chain.current_index]

    def remaining_retries(self) -> int:
        """Calculate remaining retries for current agent."""
        chain = self.chain_for_phase(self.phase)
        if chain is None:
            return 0
        return max(0, 3 - chain.retries)

    def advance_agent(self) -> PipelineState:
        """Advance to the next agent in the fallback chain."""
        chain = self.chain_for_phase(self.phase)
        if chain is None:
            return self
        new_chain = AgentChainState(
            agents=chain.agents,
            current_index=min(chain.current_index + 1, len(chain.agents) - 1),
            retries=0,
        )
        return self.with_phase_chain(self.phase, new_chain)

    def chain_for_phase(self, phase: PipelinePhase | str) -> AgentChainState | None:
        """Get the tracked agent chain state for a phase, if any."""
        return self.phase_chains.get(str(phase))

    def with_phase_chain(
        self,
        phase: PipelinePhase | str,
        chain: AgentChainState,
    ) -> PipelineState:
        """Return a copy with the chain state for the given phase updated."""
        phase_key = str(phase)
        new_chains = {**self.phase_chains, phase_key: chain}
        return self.copy_with(phase_chains=new_chains)

    def with_drain(self, drain: DrainName | None) -> PipelineState:
        """Return a copy with the current_drain set."""
        return self.copy_with(current_drain=drain)

    def get_loop_iteration(self, field_name: str) -> int:
        """Get the loop iteration counter for a policy-declared iteration field.

        Checks loop_iterations dict first. Falls back to legacy typed fields for
        the two built-in counters.

        Args:
            field_name: The iteration_state_field value from PhaseLoopPolicy.

        Returns:
            Current iteration count.
        """
        if field_name in self.loop_iterations:
            return self.loop_iterations[field_name]
        if field_name == "development_analysis_iteration":
            return self.development_analysis_iteration
        if field_name == "review_analysis_iteration":
            return self.review_analysis_iteration
        return 0

    def get_max_loop_iteration(self, field_name: str) -> int:
        """Get the runtime cap for a loop iteration field from state.

        Returns the state-level maximum. For built-in counters, falls back to
        the legacy typed cap field.

        Args:
            field_name: The iteration_state_field value from PhaseLoopPolicy.

        Returns:
            Maximum iteration count.

        Raises:
            AttributeError: If field_name is not a known counter.
        """
        if field_name in self.loop_caps:
            return self.loop_caps[field_name]
        if field_name == "development_analysis_iteration":
            return self.max_development_analysis_iterations
        if field_name == "review_analysis_iteration":
            return self.max_review_analysis_iterations
        raise AttributeError(
            f"Unknown loop cap for field '{field_name}'. "
            f"Declare it in pipeline.loop_counters and initialize in loop_caps."
        )

    def with_loop_iteration(self, field_name: str, value: int) -> PipelineState:
        """Return a copy with the specified loop iteration field set to value.

        Updates loop_iterations dict. Also updates legacy typed fields for the
        two built-in counters to keep display/checkpoint in sync.

        Args:
            field_name: The iteration_state_field value from PhaseLoopPolicy.
            value: New iteration count.

        Returns:
            New PipelineState with the iteration counter updated.
        """
        updates: dict[str, object] = {
            "loop_iterations": {**self.loop_iterations, field_name: value},
        }
        if field_name == "development_analysis_iteration":
            updates["development_analysis_iteration"] = value
        elif field_name == "review_analysis_iteration":
            updates["review_analysis_iteration"] = value
        return self.copy_with(**updates)

    def get_budget_remaining(self, counter_name: str) -> int:
        """Get the remaining budget for a policy-declared budget counter.

        Args:
            counter_name: The budget counter name from PhaseCommitPolicy.increments_counter.

        Returns:
            Remaining budget count.
        """
        if counter_name in self.budget_remaining:
            return self.budget_remaining[counter_name]
        if counter_name == "iteration":
            return self.development_budget_remaining
        if counter_name == "reviewer_pass":
            return self.review_budget_remaining
        return 0

    def get_outer_progress(self, counter_name: str) -> int:
        """Get the completed cycle count for a policy-declared budget counter."""
        if counter_name in self.outer_progress:
            return self.outer_progress[counter_name]
        if counter_name == "iteration":
            return self.iteration
        if counter_name == "reviewer_pass":
            return self.reviewer_pass
        return 0

    def with_budget_remaining(self, counter_name: str, value: int) -> PipelineState:
        """Return a copy with the specified budget counter set to value."""
        updates: dict[str, object] = {
            "budget_remaining": {**self.budget_remaining, counter_name: value},
        }
        if counter_name == "iteration":
            updates["development_budget_remaining"] = value
        elif counter_name == "reviewer_pass":
            updates["review_budget_remaining"] = value
        return self.copy_with(**updates)

    def with_outer_progress(self, counter_name: str, value: int) -> PipelineState:
        """Return a copy with the specified outer progress counter set to value."""
        updates: dict[str, object] = {
            "outer_progress": {**self.outer_progress, counter_name: value},
        }
        if counter_name == "iteration":
            updates["iteration"] = value
        elif counter_name == "reviewer_pass":
            updates["reviewer_pass"] = value
        return self.copy_with(**updates)

    def copy_with(self, **updates: object) -> PipelineState:
        """Return a copy with updates applied in a typed-safe manner."""
        if self.work_units and "work_units" in updates and updates["work_units"] != self.work_units:
            updates = {k: v for k, v in updates.items() if k != "work_units"}
        return self.model_copy(update=updates)
