"""Tests for session-preserving retry contract through reducer and RecoveryController."""

from __future__ import annotations

import tempfile
from functools import lru_cache
from pathlib import Path
from unittest.mock import MagicMock

from ralph.phases import PhaseContext
from ralph.phases.analysis import handle_generic_analysis_phase
from ralph.phases.execution import handle_execution_phase
from ralph.phases.review import handle_review
from ralph.pipeline.effects import InvokeAgentEffect
from ralph.pipeline.events import PhaseFailureEvent
from ralph.pipeline.state import AgentChainState, PipelineState
from ralph.policy.loader import load_policy


@lru_cache(maxsize=1)
def _default_policy_bundle() -> object:
    with tempfile.TemporaryDirectory() as tmp:
        return load_policy(Path(tmp) / ".agent")


def _default_policy_context(workspace: object=None) -> PhaseContext:
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


class TestPhaseHandlerRetryInSessionFlags:
    """Phase handlers must emit retry_in_session=True for missing artifact failures."""

    def test_development_missing_planning_artifact_is_retry_in_session(self) -> None:

        effect = InvokeAgentEffect(agent_name="dev", phase="development", prompt_file="dev.txt")
        ctx = _default_policy_context()

        events = handle_execution_phase(effect, ctx)

        failure_events = [e for e in events if isinstance(e, PhaseFailureEvent)]
        assert len(failure_events) == 1
        assert failure_events[0].retry_in_session is True

    def test_development_analysis_missing_artifact_is_retry_in_session(self) -> None:

        effect = MagicMock(spec=InvokeAgentEffect)
        effect.phase = "development_analysis"
        effect.drain = None
        ctx = MagicMock()
        ctx.workspace.exists.return_value = False

        events = handle_generic_analysis_phase(effect, ctx)

        failure_events = [e for e in events if isinstance(e, PhaseFailureEvent)]
        assert len(failure_events) == 1
        assert failure_events[0].retry_in_session is True

    def test_review_analysis_missing_artifact_is_retry_in_session(self) -> None:

        effect = MagicMock(spec=InvokeAgentEffect)
        effect.phase = "review_analysis"
        effect.drain = None
        ctx = MagicMock()
        ctx.workspace.exists.return_value = False

        events = handle_generic_analysis_phase(effect, ctx)

        failure_events = [e for e in events if isinstance(e, PhaseFailureEvent)]
        assert len(failure_events) == 1
        assert failure_events[0].retry_in_session is True

    def test_review_missing_issues_artifact_is_retry_in_session(self) -> None:

        effect = MagicMock(spec=InvokeAgentEffect)
        effect.phase = "review"
        ctx = MagicMock()
        ctx.workspace.exists.return_value = False

        events = handle_review(effect, ctx)

        failure_events = [e for e in events if isinstance(e, PhaseFailureEvent)]
        assert len(failure_events) == 1
        assert failure_events[0].retry_in_session is True

    def test_planning_missing_plan_artifact_is_retry_in_session(self) -> None:

        effect = InvokeAgentEffect(agent_name="planner", phase="planning", prompt_file="plan.txt")
        ctx = _default_policy_context()

        events = handle_execution_phase(effect, ctx)

        failure_events = [e for e in events if isinstance(e, PhaseFailureEvent)]
        assert len(failure_events) == 1
        assert failure_events[0].retry_in_session is True
