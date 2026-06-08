"""Tests for session-preserving retry contract through reducer and RecoveryController."""

from __future__ import annotations

import tempfile
from functools import lru_cache
from pathlib import Path
from unittest.mock import MagicMock

from ralph.phases import PhaseContext
from ralph.pipeline.state import AgentChainState, PipelineState
from ralph.policy.loader import load_policy
from ralph.recovery.controller import FailureContext, RecoveryController, RecoveryControllerOptions


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


class TestRecoveryControllerSessionPreservingRetry:
    """Via RecoveryController.handle() directly."""

    def _make_controller(self) -> RecoveryController:
        return RecoveryController(options=RecoveryControllerOptions(cycle_cap=10))

    def test_retry_in_session_sets_pending_flag_when_session_id_present(self) -> None:
        controller = self._make_controller()
        state = _state_with_session()

        new_state, _, _ = controller.handle(
            state,
            "missing artifact",
            FailureContext(phase="development_analysis", agent="claude", retry_in_session=True),
        )

        assert new_state.agent_retry_intent.action == "resume"
        assert new_state.agent_retry_intent.session_id == "sess-abc123"

    def test_retry_in_session_no_effect_when_session_id_absent(self) -> None:
        controller = self._make_controller()
        state = _state_without_session()

        new_state, _, _ = controller.handle(
            state,
            "missing artifact",
            FailureContext(phase="development_analysis", agent="claude", retry_in_session=True),
        )

        assert new_state.agent_retry_intent.action is None

    def test_retry_in_session_false_never_sets_pending_flag(self) -> None:
        controller = self._make_controller()
        state = _state_with_session()

        new_state, _, _ = controller.handle(
            state,
            "missing artifact",
            FailureContext(phase="development_analysis", agent="claude", retry_in_session=False),
        )

        assert new_state.agent_retry_intent.action is None
