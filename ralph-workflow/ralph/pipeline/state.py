"""Immutable pipeline state model.

This module defines PipelineState - the single source of truth for pipeline
execution progress. It serves dual purposes:
1. Runtime State: Tracks current phase, iteration counters, agent chain state
2. Checkpoint Payload: Serializes to JSON for resume functionality

PipelineState is IMMUTABLE from the reducer's perspective. State transitions
occur exclusively through the reduce function.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ralph.config.enums import (
    PHASE_COMPLETE,
    PHASE_DEVELOPMENT,
    PHASE_REVIEW,
    PipelinePhase,
)
from ralph.pipeline.work_units import WorkUnit  # noqa: TC001
from ralph.pipeline.worker_state import WorkerState  # noqa: TC001

if TYPE_CHECKING:
    from ralph.policy.models import DrainName


_PHASE_CHAIN_FIELDS: dict[str, str] = {
    "planning": "planning_chain",
    PHASE_DEVELOPMENT: "dev_chain",
    "development_analysis": "dev_analysis_chain",
    PHASE_REVIEW: "rev_chain",
    "review_analysis": "review_analysis_chain",
    "fix": "fix_chain",
}


class _FrozenPipelineStateModel(BaseModel):  # type: ignore[explicit-any]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library
    """Private base for frozen pipeline state models.

    Owns ``model_config = ConfigDict(frozen=True)`` once so descendants do not
    repeat it. Pydantic v2 inherits ``model_config`` when descendants do not
    declare one of their own.
    """

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
        """Return a copy with retries incremented by 1; agents and current_index unchanged."""
        return AgentChainState(
            agents=self.agents,
            current_index=self.current_index,
            retries=self.retries + 1,
        )

    def with_advance(self) -> AgentChainState:
        """Return a copy advanced to the next agent with retries reset to 0.

        Callers MUST check that current_index + 1 < len(agents) before invoking.
        """
        return AgentChainState(
            agents=self.agents,
            current_index=self.current_index + 1,
            retries=0,
        )


class RebaseState(_FrozenPipelineStateModel):  # type: ignore[explicit-any]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library
    """State for git rebase operations.

    Attributes:
        pending: Whether a rebase is pending.
        in_progress: Whether a rebase is in progress.
        completed: Whether rebase has completed.
    """

    pending: bool = False
    in_progress: bool = False
    completed: bool = False


class CommitState(_FrozenPipelineStateModel):  # type: ignore[explicit-any]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library
    """State for commit operations.

    Attributes:
        message_prepared: Whether commit message has been prepared.
        diff_prepared: Whether commit diff has been prepared.
        agent_invoked: Whether commit agent has been invoked.
    """

    message_prepared: bool = False
    diff_prepared: bool = False
    agent_invoked: bool = False


class RunMetrics(_FrozenPipelineStateModel):  # type: ignore[explicit-any]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library
    """Run-level execution metrics.

    Attributes:
        total_agent_calls: Total number of agent invocations.
        total_continuations: Total number of continuation attempts.
        total_fallbacks: Total number of agent fallbacks.
        total_retries: Total number of retries.
    """

    total_agent_calls: int = 0
    total_continuations: int = 0
    total_fallbacks: int = 0
    total_retries: int = 0


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

    Attributes:
        phase: Current pipeline phase (string from pipeline.toml).
        previous_phase: Previous pipeline phase.
        iteration: Current development iteration.
        total_iterations: Total number of development iterations.
        reviewer_pass: Current reviewer pass number.
        total_reviewer_passes: Total number of reviewer passes.
        review_issues_found: Whether review found issues requiring fix.
        planning_chain: Planning agent chain state.
        dev_chain: Development agent chain state.
        dev_analysis_chain: Development analysis agent chain state.
        rev_chain: Review agent chain state.
        review_analysis_chain: Review analysis agent chain state.
        fix_chain: Fix agent chain state.
        rebase: Git rebase state.
        commit: Commit state.
        metrics: Run-level execution metrics.
        checkpoint_saved_count: Count of checkpoint saves.
        recovery_epoch: Recovery epoch counter.
        interrupted_by_user: Whether interrupted by user (Ctrl+C).
        git_auth_configured: Whether git auth has been configured.
        pr_created: Whether PR has been created.
        pr_url: URL of created PR.
        push_count: Count of successful push operations.
        last_error: Last error message.
        last_reviewed_sha: HEAD SHA captured after the last review pass
            completed. Used to skip review when no new commits exist.
        policy_entry_phase: Entry phase from the loaded pipeline policy.
        development_budget_remaining: Remaining development iterations budget.
        review_budget_remaining: Remaining review passes budget.
        current_drain: Currently active drain (derived from policy at runtime).
        development_analysis_iteration: Current development analysis loop iteration.
        max_development_analysis_iterations: Maximum development analysis loop budget.
        review_analysis_iteration: Current review analysis loop iteration.
        max_review_analysis_iterations: Maximum review analysis loop budget.
        recovery_cycle_count: Number of full-chain exhaustion recovery cycles.
        fallover_history: History of agent fallover events.
        last_failure_category: Category of the most recent classified failure.
        last_connectivity_state: Last observed connectivity state string.
        recovery_cycle_cap: Maximum recovery cycles before pipeline exits.
        last_retry_delay_ms: Pending retry delay in ms (set by controller, consumed by runner).
    """

    phase: PipelinePhase = "planning"
    previous_phase: PipelinePhase | None = None
    iteration: int = 0
    total_iterations: int = 5
    reviewer_pass: int = 0
    total_reviewer_passes: int = 2
    review_issues_found: bool = False
    planning_chain: AgentChainState = Field(default_factory=AgentChainState)
    dev_chain: AgentChainState = Field(default_factory=AgentChainState)
    dev_analysis_chain: AgentChainState = Field(default_factory=AgentChainState)
    rev_chain: AgentChainState = Field(default_factory=AgentChainState)
    review_analysis_chain: AgentChainState = Field(default_factory=AgentChainState)
    fix_chain: AgentChainState = Field(default_factory=AgentChainState)
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
    development_budget_remaining: int = 0
    review_budget_remaining: int = 0
    current_drain: str | None = None

    # Analysis iteration tracking (per dev/review cycle)
    development_analysis_iteration: int = Field(default=0, ge=0)
    max_development_analysis_iterations: int = Field(default=3, ge=0)
    review_analysis_iteration: int = Field(default=0, ge=0)
    max_review_analysis_iterations: int = Field(default=2, ge=0)

    work_units: tuple[WorkUnit, ...] = Field(default_factory=tuple)
    worker_states: dict[str, WorkerState] = Field(default_factory=dict)

    # Recovery observability fields — all have defaults so legacy checkpoints load cleanly
    recovery_cycle_count: int = 0
    fallover_history: tuple[FalloverRecord, ...] = Field(default_factory=tuple)
    last_failure_category: str | None = None
    last_connectivity_state: str = "unknown"
    recovery_cycle_cap: int = Field(default=200, ge=1)
    # Runner-managed delay: not persisted to checkpoint; consumed and cleared by the main loop
    last_retry_delay_ms: int = 0
    # Session-preserving retry fields — default to safe values so legacy checkpoints load cleanly.
    # last_agent_session_id: session identifier from the most recent successful agent invocation.
    # session_preserve_retry_pending: set when the next retry should resume the captured session.
    last_agent_session_id: str | None = None
    session_preserve_retry_pending: bool = False

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

    def is_complete(self) -> bool:
        """Check if pipeline has reached a terminal success state.

        Returns:
            True only when pipeline has completed successfully.
        """
        return self.phase == PHASE_COMPLETE

    def current_agent(self) -> str | None:
        """Get the current agent for the active phase.

        Returns:
            Agent name or None if no agents available.
        """
        chain = self.chain_for_phase(self.phase)
        if chain is None:
            return None

        if not chain.agents or chain.current_index >= len(chain.agents):
            return None
        return chain.agents[chain.current_index]

    def remaining_retries(self) -> int:
        """Calculate remaining retries for current agent.

        Returns:
            Number of remaining retries.
        """
        chain = self.chain_for_phase(self.phase)
        if chain is None:
            return 0
        return max(0, 3 - chain.retries)

    def advance_agent(self) -> PipelineState:
        """Advance to the next agent in the fallback chain.

        Returns:
            New state with advanced agent index.
        """
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
        field_name = _PHASE_CHAIN_FIELDS.get(phase)
        if field_name is None:
            return None
        return cast("AgentChainState", getattr(self, field_name))

    def with_phase_chain(
        self,
        phase: PipelinePhase | str,
        chain: AgentChainState,
    ) -> PipelineState:
        """Return a copy with the chain state for the given phase updated."""
        field_name = _PHASE_CHAIN_FIELDS.get(phase)
        if field_name is None:
            return self
        return self.copy_with(**{field_name: chain})

    def with_drain(self, drain: DrainName | None) -> PipelineState:
        """Return a copy with the current_drain set.

        Args:
            drain: The drain name to set.

        Returns:
            New PipelineState with current_drain updated.
        """
        return self.copy_with(current_drain=drain)

    def copy_with(self, **updates: object) -> PipelineState:
        """Return a copy with updates applied in a typed-safe manner.

        Note: work_units is set exactly once during the planning phase and is
        intentionally immutable after that point.  Any attempt to overwrite an
        already-populated work_units via copy_with is silently dropped here to
        guard against accidental corruption of the plan between fan-out waves.
        """
        if self.work_units and "work_units" in updates and updates["work_units"] != self.work_units:
            updates = {k: v for k, v in updates.items() if k != "work_units"}
        return self.model_copy(update=updates)
