"""Immutable pipeline state model.

This module defines PipelineState - the single source of truth for pipeline
execution progress. It serves dual purposes:
1. Runtime State: Tracks current phase, iteration counters, agent chain state
2. Checkpoint Payload: Serializes to JSON for resume functionality

PipelineState is IMMUTABLE from the reducer's perspective. State transitions
occur exclusively through the reduce function.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ralph.config.enums import (
    PHASE_COMPLETE,
    PHASE_DEVELOPMENT,
    PHASE_FAILED,
    PHASE_REVIEW,
    PipelinePhase,
)
from ralph.pipeline.work_units import WorkUnit  # noqa: TC001

if TYPE_CHECKING:
    from ralph.policy.models import DrainName


class AgentChainState(BaseModel):  # type: ignore[explicit-any]
    """State for agent fallback chain management.

    Attributes:
        agents: List of agent names in the fallback chain.
        current_index: Current agent index being used.
        retries: Number of retries for current agent.
    """

    model_config = ConfigDict(frozen=True)

    agents: list[str] = Field(default_factory=list)
    current_index: int = 0
    retries: int = 0


class RebaseState(BaseModel):  # type: ignore[explicit-any]
    """State for git rebase operations.

    Attributes:
        pending: Whether a rebase is pending.
        in_progress: Whether a rebase is in progress.
        completed: Whether rebase has completed.
    """

    model_config = ConfigDict(frozen=True)

    pending: bool = False
    in_progress: bool = False
    completed: bool = False


class CommitState(BaseModel):  # type: ignore[explicit-any]
    """State for commit operations.

    Attributes:
        message_prepared: Whether commit message has been prepared.
        diff_prepared: Whether commit diff has been prepared.
        agent_invoked: Whether commit agent has been invoked.
    """

    model_config = ConfigDict(frozen=True)

    message_prepared: bool = False
    diff_prepared: bool = False
    agent_invoked: bool = False


class RunMetrics(BaseModel):  # type: ignore[explicit-any]
    """Run-level execution metrics.

    Attributes:
        total_agent_calls: Total number of agent invocations.
        total_continuations: Total number of continuation attempts.
        total_fallbacks: Total number of agent fallbacks.
        total_retries: Total number of retries.
    """

    model_config = ConfigDict(frozen=True)

    total_agent_calls: int = 0
    total_continuations: int = 0
    total_fallbacks: int = 0
    total_retries: int = 0


class ContinuationState(BaseModel):  # type: ignore[explicit-any]
    """Continuation state for development iterations.

    Attributes:
        active: Whether a continuation is active.
        previous_status: The previous development status.
        context_write_pending: Whether context write is pending.
    """

    model_config = ConfigDict(frozen=True)

    active: bool = False
    previous_status: str | None = None
    context_write_pending: bool = False


class PipelineState(BaseModel):  # type: ignore[explicit-any]
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
        continuation: Continuation state.
        metrics: Run-level execution metrics.
        checkpoint_saved_count: Count of checkpoint saves.
        recovery_epoch: Recovery epoch counter.
        interrupted_by_user: Whether interrupted by user (Ctrl+C).
        git_auth_configured: Whether git auth has been configured.
        pr_created: Whether PR has been created.
        pr_url: URL of created PR.
        push_count: Count of successful push operations.
        last_error: Last error message.
        policy_entry_phase: Entry phase from the loaded pipeline policy.
        development_budget_remaining: Remaining development iterations budget.
        review_budget_remaining: Remaining review passes budget.
        current_drain: Currently active drain (derived from policy at runtime).
    """

    model_config = ConfigDict(frozen=True)

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
    continuation: ContinuationState = Field(default_factory=ContinuationState)
    metrics: RunMetrics = Field(default_factory=RunMetrics)
    checkpoint_saved_count: int = 0
    recovery_epoch: int = 0
    interrupted_by_user: bool = False
    git_auth_configured: bool = False
    pr_created: bool = False
    pr_url: str | None = None
    push_count: int = 0
    last_error: str | None = None

    # Policy-derived fields (set at startup and after phase transitions)
    policy_entry_phase: PipelinePhase = "planning"
    development_budget_remaining: int = 0
    review_budget_remaining: int = 0
    current_drain: str | None = None

    work_units: tuple[WorkUnit, ...] = Field(default_factory=tuple)

    @field_validator("work_units", mode="before")
    @classmethod
    def _coerce_work_units(cls, v: object) -> tuple[WorkUnit, ...]:
        if v is None:
            return ()
        if isinstance(v, list):
            return tuple(v)
        return v  # type: ignore[return-value]

    def is_complete(self) -> bool:
        """Check if pipeline has reached a terminal state.

        Returns:
            True if pipeline is complete or failed.
        """
        return self.phase in (PHASE_COMPLETE, PHASE_FAILED)

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
        phase_to_chain = {
            "planning": self.planning_chain,
            PHASE_DEVELOPMENT: self.dev_chain,
            "development_analysis": self.dev_analysis_chain,
            PHASE_REVIEW: self.rev_chain,
            "review_analysis": self.review_analysis_chain,
            "fix": self.fix_chain,
        }
        return phase_to_chain.get(phase)

    def with_phase_chain(
        self,
        phase: PipelinePhase | str,
        chain: AgentChainState,
    ) -> PipelineState:
        """Return a copy with the chain state for the given phase updated."""
        phase_to_field = {
            "planning": "planning_chain",
            PHASE_DEVELOPMENT: "dev_chain",
            "development_analysis": "dev_analysis_chain",
            PHASE_REVIEW: "rev_chain",
            "review_analysis": "review_analysis_chain",
            "fix": "fix_chain",
        }
        field_name = phase_to_field.get(phase)
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
        """Return a copy with updates applied in a typed-safe manner."""
        if self.work_units and "work_units" in updates and updates["work_units"] != self.work_units:
            updates = {k: v for k, v in updates.items() if k != "work_units"}
        return self.model_copy(update=updates)
