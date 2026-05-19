"""Tests for session-preserving retry contract through reducer and RecoveryController."""

from __future__ import annotations

import tempfile
from functools import lru_cache
from pathlib import Path
from unittest.mock import MagicMock

from ralph.phases import PhaseContext
from ralph.pipeline.events import PhaseFailureEvent
from ralph.pipeline.reducer import reduce as reducer_reduce
from ralph.pipeline.state import AgentChainState, PipelineState
from ralph.policy.loader import load_policy


@lru_cache(maxsize=1)
def _default_policy_bundle() -> object:
    with tempfile.TemporaryDirectory() as tmp:
        return load_policy(Path(tmp) / ".agent")


def _default_policy_context(workspace: object = None) -> PhaseContext:
    policy = _default_policy_bundle()
    ws = workspace if workspace is not None else MagicMock()
    if workspace is None:
        ws.exists.return_value = False
    return PhaseContext.construct(
        workspace=ws,
        registry=object(),
        chain_manager=object(),
        pipeline_policy=policy.pipeline,
        artifacts_policy=policy.artifacts,
        agents_policy=object(),
    )


def _state_with_session(phase: str = "development_analysis") -> PipelineState:
    return PipelineState(
        phase=phase,
        phase_chains={phase: AgentChainState(agents=["claude"], current_index=0, retries=0)},
        last_agent_session_id="sess-abc123",
    )


def _state_without_session(phase: str = "development_analysis") -> PipelineState:
    return PipelineState(
        phase=phase,
        phase_chains={phase: AgentChainState(agents=["claude"], current_index=0, retries=0)},
        last_agent_session_id=None,
    )


class TestReducerSessionPreservingRetry:
    """Via the legacy reducer path (no RecoveryController)."""

    def test_retry_in_session_with_session_id_sets_pending_flag(self) -> None:
        state = _state_with_session()
        event = PhaseFailureEvent(
            phase="development_analysis",
            reason="missing artifact",
            recoverable=True,
            retry_in_session=True,
        )
        new_state, _ = reducer_reduce(state, event)
        assert new_state.session_preserve_retry_pending is True

    def test_retry_in_session_without_session_id_does_not_set_pending_flag(self) -> None:
        state = _state_without_session()
        event = PhaseFailureEvent(
            phase="development_analysis",
            reason="missing artifact",
            recoverable=True,
            retry_in_session=True,
        )
        new_state, _ = reducer_reduce(state, event)
        assert new_state.session_preserve_retry_pending is False

    def test_retry_in_session_false_never_sets_pending_flag(self) -> None:
        state = _state_with_session()
        event = PhaseFailureEvent(
            phase="development_analysis",
            reason="missing artifact",
            recoverable=True,
            retry_in_session=False,
        )
        new_state, _ = reducer_reduce(state, event)
        assert new_state.session_preserve_retry_pending is False

    def test_chain_retries_increments_on_session_preserving_retry(self) -> None:
        state = _state_with_session()
        event = PhaseFailureEvent(
            phase="development_analysis",
            reason="missing artifact",
            recoverable=True,
            retry_in_session=True,
        )
        new_state, _ = reducer_reduce(state, event)
        chain = new_state.chain_for_phase("development_analysis")
        assert chain is not None
        assert chain.retries == 1

    def test_session_id_preserved_across_retry(self) -> None:
        state = _state_with_session()
        event = PhaseFailureEvent(
            phase="development_analysis",
            reason="missing artifact",
            recoverable=True,
            retry_in_session=True,
        )
        new_state, _ = reducer_reduce(state, event)
        assert new_state.last_agent_session_id == "sess-abc123"
