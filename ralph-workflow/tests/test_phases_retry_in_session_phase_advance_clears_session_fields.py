"""Tests for session-preserving retry contract through reducer and RecoveryController."""

from __future__ import annotations

import tempfile
from functools import lru_cache
from pathlib import Path
from unittest.mock import MagicMock

from ralph.phases import PhaseContext
from ralph.pipeline.events import PhaseFailureEvent, PipelineEvent
from ralph.pipeline.reducer import reduce as reducer_reduce
from ralph.pipeline.state import AgentChainState, PipelineState
from ralph.policy.loader import load_policy
from ralph.policy.models import (
    PhaseDefinition,
    PhaseTransition,
    PhaseWorkflowFallback,
    PipelinePolicy,
)


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


def _minimal_analysis_policy() -> PipelinePolicy:
    return PipelinePolicy(
        phases={
            "development_analysis": PhaseDefinition(
                drain="development_analysis",
                role="analysis",
                transitions=PhaseTransition(
                    on_success="done",
                    on_loopback="development_analysis",
                ),
            ),
            "done": PhaseDefinition(
                drain="done",
                role="terminal",
                terminal_outcome="success",
                transitions=PhaseTransition(on_success="done", on_loopback="done"),
            ),
        },
        entry_phase="development_analysis",
        terminal_phase="done",
    )


def _terminal_transition_policy() -> PipelinePolicy:
    return PipelinePolicy(
        phases={
            "development": PhaseDefinition(
                drain="development",
                role="execution",
                transitions=PhaseTransition(on_success="done", on_failure="failed_terminal"),
            ),
            "done": PhaseDefinition(
                drain="done",
                role="terminal",
                terminal_outcome="success",
                transitions=PhaseTransition(on_success="done", on_loopback="done"),
            ),
            "failed_terminal": PhaseDefinition(
                drain="failed_terminal",
                role="terminal",
                terminal_outcome="failure",
                transitions=PhaseTransition(
                    on_success="failed_terminal", on_loopback="failed_terminal"
                ),
            ),
        },
        entry_phase="development",
        terminal_phase="done",
        recovery={"failed_route": "failed_terminal"},
    )


def _workflow_fallback_policy() -> PipelinePolicy:
    return PipelinePolicy(
        phases={
            "development": PhaseDefinition(
                drain="development",
                role="execution",
                transitions=PhaseTransition(on_success="done", on_failure="failed_terminal"),
                workflow_fallback=PhaseWorkflowFallback(target="planning"),
            ),
            "planning": PhaseDefinition(
                drain="planning",
                role="execution",
                transitions=PhaseTransition(on_success="done", on_failure="failed_terminal"),
            ),
            "done": PhaseDefinition(
                drain="done",
                role="terminal",
                terminal_outcome="success",
                transitions=PhaseTransition(on_success="done", on_loopback="done"),
            ),
            "failed_terminal": PhaseDefinition(
                drain="failed_terminal",
                role="terminal",
                terminal_outcome="failure",
                transitions=PhaseTransition(
                    on_success="failed_terminal", on_loopback="failed_terminal"
                ),
            ),
        },
        entry_phase="development",
        terminal_phase="done",
        recovery={"failed_route": "failed_terminal"},
    )


class TestPhaseAdvanceClearsSessionFields:
    """Phase advance must clear session fields to prevent cross-phase session leaks."""

    def test_advance_phase_clears_last_agent_session_id(self) -> None:
        state = PipelineState(
            phase="development_analysis",
            phase_chains={
                "development_analysis": AgentChainState(
                    agents=["claude"], current_index=0, retries=0
                )
            },
            last_agent_session_id="sess-to-clear",
        )
        policy = _minimal_analysis_policy()
        new_state, _ = reducer_reduce(state, PipelineEvent.ANALYSIS_SUCCESS, pipeline_policy=policy)
        assert new_state.last_agent_session_id is None

    def test_advance_phase_clears_session_preserve_retry_pending(self) -> None:
        state = PipelineState(
            phase="development_analysis",
            phase_chains={
                "development_analysis": AgentChainState(
                    agents=["claude"], current_index=0, retries=0
                )
            },
            last_agent_session_id="sess-x",
            session_preserve_retry_pending=True,
        )
        policy = _minimal_analysis_policy()
        new_state, _ = reducer_reduce(state, PipelineEvent.ANALYSIS_SUCCESS, pipeline_policy=policy)
        assert new_state.session_preserve_retry_pending is False

    def test_complete_clears_session_fields_on_terminal_success(self) -> None:
        state = PipelineState(
            phase="development",
            phase_chains={
                "development": AgentChainState(agents=["claude"], current_index=0, retries=0)
            },
            last_agent_session_id="sess-to-clear",
            session_preserve_retry_pending=True,
        )

        new_state, _ = reducer_reduce(
            state,
            PipelineEvent.COMPLETE,
            pipeline_policy=_terminal_transition_policy(),
        )

        assert new_state.phase == "done"
        assert new_state.last_agent_session_id is None
        assert new_state.session_preserve_retry_pending is False

    def test_failed_clears_session_fields_on_terminal_failure(self) -> None:
        state = PipelineState(
            phase="development",
            phase_chains={
                "development": AgentChainState(agents=["claude"], current_index=0, retries=0)
            },
            last_agent_session_id="sess-to-clear",
            session_preserve_retry_pending=True,
            last_error="boom",
        )

        new_state, _ = reducer_reduce(
            state,
            PipelineEvent.FAILED,
            pipeline_policy=_terminal_transition_policy(),
        )

        assert new_state.phase == "failed_terminal"
        assert new_state.last_agent_session_id is None
        assert new_state.session_preserve_retry_pending is False

    def test_workflow_fallback_clears_session_fields(self) -> None:
        state = PipelineState(
            phase="development",
            phase_chains={
                "development": AgentChainState(agents=["claude"], current_index=0, retries=0)
            },
            last_agent_session_id="sess-to-clear",
            session_preserve_retry_pending=True,
        )

        new_state, _ = reducer_reduce(
            state,
            PhaseFailureEvent(
                phase="development",
                reason="non recoverable",
                recoverable=False,
            ),
            pipeline_policy=_workflow_fallback_policy(),
        )

        assert new_state.phase == "planning"
        assert new_state.last_agent_session_id is None
        assert new_state.session_preserve_retry_pending is False
