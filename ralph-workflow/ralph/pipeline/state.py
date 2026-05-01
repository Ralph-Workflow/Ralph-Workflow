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

Legacy checkpoint fields (budget fields only) are migrated to the generic
dicts at deserialise time via the _migrate_legacy_state_fields model_validator.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final, cast

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ralph.config.enums import PipelinePhase  # noqa: TC001
from ralph.pipeline.work_units import WorkUnit  # noqa: TC001
from ralph.pipeline.worker_state import WorkerState  # noqa: TC001

if TYPE_CHECKING:
    from ralph.policy.models import DrainName, PipelinePolicy

_UNSET_PHASE: Final[str] = "__unset__"

def _migrate_counter_field(
    d: dict[str, object],
    target: dict[str, object],
    legacy_field: str,
    counter_name: str,
) -> None:
    if counter_name not in target and legacy_field in d:
        target[counter_name] = d[legacy_field]


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
        budget_caps: Max budget keyed by budget counter name (seeded from policy).
        outer_progress: Completed cycle counts keyed by budget counter name.
    """

    phase: PipelinePhase = _UNSET_PHASE
    previous_phase: PipelinePhase | None = None

    # Review outcome tracking (replaces direct review_issues_found writes)
    review_outcome: str | None = None

    # Generic per-phase chain state (keyed by canonical phase name from policy)
    phase_chains: dict[str, AgentChainState] = Field(default_factory=dict)

    # Generic loop iteration tracking (keyed by iteration_state_field from loop_policy)
    loop_iterations: dict[str, int] = Field(default_factory=dict)
    loop_caps: dict[str, int] = Field(default_factory=dict)

    # Generic budget counter tracking (keyed by budget counter name from budget_counters)
    budget_remaining: dict[str, int] = Field(default_factory=dict)
    budget_caps: dict[str, int] = Field(default_factory=dict)
    outer_progress: dict[str, int] = Field(default_factory=dict)

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
    policy_entry_phase: PipelinePhase = _UNSET_PHASE
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

    @model_validator(mode="after")
    def _validate_phase_set(self) -> PipelineState:
        if self.phase == _UNSET_PHASE:
            raise ValueError(
                "PipelineState requires phase to be set from PipelinePolicy.entry_phase "
                "before construction; use PipelineState.from_policy(policy) "
                "or pass phase= explicitly."
            )
        return self

    @classmethod
    def from_policy(cls, policy: PipelinePolicy, **overrides: object) -> PipelineState:
        """Construct initial pipeline state from a loaded PipelinePolicy.

        The entry phase is derived from policy.entry_phase so no workflow
        entry semantics are embedded in this class.
        """
        return cls(
            phase=policy.entry_phase,
            policy_entry_phase=policy.entry_phase,
            **overrides,  # type: ignore[arg-type]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library
        )

    @model_validator(mode="before")
    @classmethod
    def _migrate_legacy_state_fields(cls, data: object) -> object:
        """Migrate legacy checkpoint fields into generic dicts.

        Handles old checkpoints that stored:
        - Typed chain fields → migrated into phase_chains
        - Legacy budget fields → migrated into budget_remaining / outer_progress
        """
        if not isinstance(data, dict):
            return data
        d = cast("dict[str, object]", dict(data))

        # Migrate legacy budget fields into budget_remaining, budget_caps, and outer_progress
        _raw_br = d.get("budget_remaining")
        budget_remaining: dict[str, object] = dict(
            cast("dict[str, object]", _raw_br) if _raw_br is not None else {}
        )
        _raw_bc = d.get("budget_caps")
        budget_caps_data: dict[str, object] = dict(
            cast("dict[str, object]", _raw_bc) if _raw_bc is not None else {}
        )
        _raw_op = d.get("outer_progress")
        outer_progress_data: dict[str, object] = dict(
            cast("dict[str, object]", _raw_op) if _raw_op is not None else {}
        )
        _migrate_counter_field(d, budget_remaining, "development_budget_remaining", "iteration")
        _migrate_counter_field(d, budget_remaining, "review_budget_remaining", "reviewer_pass")
        _migrate_counter_field(d, budget_caps_data, "total_iterations", "iteration")
        _migrate_counter_field(d, budget_caps_data, "total_reviewer_passes", "reviewer_pass")
        _migrate_counter_field(d, outer_progress_data, "iteration", "iteration")
        _migrate_counter_field(d, outer_progress_data, "reviewer_pass", "reviewer_pass")
        d["budget_remaining"] = budget_remaining
        d["budget_caps"] = budget_caps_data
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

    @field_validator("budget_caps", mode="before")
    @classmethod
    def _coerce_budget_caps(cls, v: object) -> dict[str, int]:
        if v is None:
            return {}
        if isinstance(v, dict):
            return {str(k): int(val) for k, val in v.items()}
        raise TypeError(f"Expected dict for budget_caps, got {type(v).__name__!r}")

    @field_validator("outer_progress", mode="before")
    @classmethod
    def _coerce_outer_progress(cls, v: object) -> dict[str, int]:
        if v is None:
            return {}
        if isinstance(v, dict):
            return {str(k): int(val) for k, val in v.items()}
        raise TypeError(f"Expected dict for outer_progress, got {type(v).__name__!r}")

    def is_complete(self, policy: PipelinePolicy) -> bool:
        """Check if pipeline has reached a terminal success state.

        Args:
            policy: PipelinePolicy. Compares current phase against
                policy.terminal_phase to determine completion.

        Raises:
            RuntimeError: When policy is None (routing requires loaded policy).
        """
        return self.phase == policy.terminal_phase

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

        Args:
            field_name: The iteration_state_field value from PhaseLoopPolicy.

        Returns:
            Current iteration count (0 when not yet set).
        """
        return self.loop_iterations.get(field_name, 0)

    def with_loop_iteration(self, field_name: str, value: int) -> PipelineState:
        """Return a copy with the specified loop iteration field set to value.

        Args:
            field_name: The iteration_state_field value from PhaseLoopPolicy.
            value: New iteration count.

        Returns:
            New PipelineState with the iteration counter updated.
        """
        return self.copy_with(
            loop_iterations={**self.loop_iterations, field_name: value},
        )

    def get_budget_remaining(self, counter_name: str) -> int:
        """Get the remaining budget for a policy-declared budget counter.

        Args:
            counter_name: The budget counter name from PhaseCommitPolicy.increments_counter.

        Returns:
            Remaining budget count.
        """
        return self.budget_remaining.get(counter_name, 0)

    def get_budget_cap(self, counter_name: str) -> int:
        """Get the budget cap for a policy-declared budget counter.

        Args:
            counter_name: The budget counter name.

        Returns:
            Budget cap (maximum allowed), or 0 if not set.
        """
        return self.budget_caps.get(counter_name, 0)

    def get_outer_progress(self, counter_name: str) -> int:
        """Get the completed cycle count for a policy-declared budget counter."""
        return self.outer_progress.get(counter_name, 0)

    def with_budget_remaining(self, counter_name: str, value: int) -> PipelineState:
        """Return a copy with the specified budget counter set to value."""
        return self.copy_with(
            budget_remaining={**self.budget_remaining, counter_name: value},
        )

    def with_outer_progress(self, counter_name: str, value: int) -> PipelineState:
        """Return a copy with the specified outer progress counter set to value."""
        return self.copy_with(outer_progress={**self.outer_progress, counter_name: value})

    def with_budget_cap(self, counter_name: str, value: int) -> PipelineState:
        """Return a copy with the specified budget cap set to value."""
        return self.copy_with(
            budget_caps={**self.budget_caps, counter_name: value},
        )

    def copy_with(self, **updates: object) -> PipelineState:
        """Return a copy with updates applied in a typed-safe manner."""
        if self.work_units and "work_units" in updates and updates["work_units"] != self.work_units:
            updates = {k: v for k, v in updates.items() if k != "work_units"}
        return self.model_copy(update=updates)
