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

from pydantic import BaseModel, ConfigDict, Field

from ralph.config.enums import (
    PHASE_COMPLETE,
    PHASE_DEVELOPMENT,
    PHASE_FAILED,
    PHASE_REVIEW,
    PipelinePhase,
)

if TYPE_CHECKING:
    from ralph.policy.models import DrainName


class AgentChainState(BaseModel):
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


class RebaseState(BaseModel):
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


class CommitState(BaseModel):
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


class RunMetrics(BaseModel):
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


class ContinuationState(BaseModel):
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


class PipelineState(BaseModel):
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
        dev_chain: Development agent chain state.
        rev_chain: Review agent chain state.
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
    dev_chain: AgentChainState = Field(default_factory=AgentChainState)
    rev_chain: AgentChainState = Field(default_factory=AgentChainState)
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
        if self.phase == PHASE_DEVELOPMENT:
            chain = self.dev_chain
        elif self.phase == PHASE_REVIEW:
            chain = self.rev_chain
        else:
            return None

        if not chain.agents or chain.current_index >= len(chain.agents):
            return None
        return chain.agents[chain.current_index]

    def remaining_retries(self) -> int:
        """Calculate remaining retries for current agent.

        Returns:
            Number of remaining retries.
        """
        chain = self.dev_chain if self.phase == PHASE_DEVELOPMENT else self.rev_chain
        return max(0, 3 - chain.retries)

    def advance_agent(self) -> PipelineState:
        """Advance to the next agent in the fallback chain.

        Returns:
            New state with advanced agent index.
        """
        if self.phase == PHASE_DEVELOPMENT:
            chain = self.dev_chain
        elif self.phase == PHASE_REVIEW:
            chain = self.rev_chain
        else:
            return self

        new_chain = AgentChainState(
            agents=chain.agents,
            current_index=min(chain.current_index + 1, len(chain.agents) - 1),
            retries=0,
        )

        if self.phase == PHASE_DEVELOPMENT:
            return self.model_copy(update={"dev_chain": new_chain})
        return self.model_copy(update={"rev_chain": new_chain})

    def with_drain(self, drain: DrainName | None) -> PipelineState:
        """Return a copy with the current_drain set.

        Args:
            drain: The drain name to set.

        Returns:
            New PipelineState with current_drain updated.
        """
        return self.model_copy(update={"current_drain": drain})
