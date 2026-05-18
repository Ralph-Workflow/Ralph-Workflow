"""Frozen sub-models used by the immutable pipeline state model."""

from __future__ import annotations

from pydantic import ConfigDict, Field

from ralph.pydantic_compat import RalphBaseModel

_FROZEN = ConfigDict(frozen=True)


class _FrozenPipelineStateModel(RalphBaseModel):
    """Private base for frozen pipeline state models."""

    model_config = _FROZEN

    class FalloverRecord(RalphBaseModel):
        """A record of a single agent fallover event persisted in pipeline state."""

        model_config = _FROZEN

        phase: str
        from_agent: str
        to_agent: str
        timestamp_iso: str

    class AgentChainState(RalphBaseModel):
        """State for agent fallback chain management.

        Attributes:
            agents: List of agent names in the fallback chain.
            current_index: Current agent index being used.
            retries: Number of retries for current agent.
        """

        model_config = _FROZEN

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

    class RebaseState(RalphBaseModel):
        """State for git rebase operations."""

        model_config = _FROZEN

        pending: bool = False
        in_progress: bool = False
        completed: bool = False

    class CommitState(RalphBaseModel):
        """State for commit operations."""

        model_config = _FROZEN

        message_prepared: bool = False
        diff_prepared: bool = False
        agent_invoked: bool = False

    class RunMetrics(RalphBaseModel):
        """Run-level execution metrics."""

        model_config = _FROZEN

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


FalloverRecord = _FrozenPipelineStateModel.FalloverRecord
AgentChainState = _FrozenPipelineStateModel.AgentChainState
RebaseState = _FrozenPipelineStateModel.RebaseState
CommitState = _FrozenPipelineStateModel.CommitState
RunMetrics = _FrozenPipelineStateModel.RunMetrics


__all__ = [
    "AgentChainState",
    "CommitState",
    "FalloverRecord",
    "RebaseState",
    "RunMetrics",
    "_FrozenPipelineStateModel",
]
