"""Tests for session-preserving retry contract through reducer and RecoveryController."""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import MagicMock

from ralph.phases import PhaseContext
from ralph.pipeline.effects import InvokeAgentEffect
from ralph.pipeline.events import PhaseFailureEvent, PipelineEvent
from ralph.pipeline.reducer import reduce as reducer_reduce
from ralph.pipeline.state import AgentChainState, PipelineState
from ralph.policy.loader import load_policy
from ralph.policy.models import PhaseDefinition, PhaseTransition, PipelinePolicy
from ralph.recovery.controller import RecoveryController


def _default_policy_context(workspace=None) -> PhaseContext:
    with tempfile.TemporaryDirectory() as tmp:
        policy = load_policy(Path(tmp) / ".agent")
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


class TestPhaseFailureEventRetryInSessionFlag:
    def test_retry_in_session_false_by_default(self) -> None:
        event = PhaseFailureEvent(
            phase="development_analysis",
            reason="missing artifact",
            recoverable=True,
        )
        assert event.retry_in_session is False

    def test_retry_in_session_true_when_set(self) -> None:
        event = PhaseFailureEvent(
            phase="development_analysis",
            reason="missing artifact",
            recoverable=True,
            retry_in_session=True,
        )
        assert event.retry_in_session is True


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


class TestRecoveryControllerSessionPreservingRetry:
    """Via RecoveryController.handle() directly."""

    def _make_controller(self) -> RecoveryController:
        return RecoveryController(cycle_cap=10)

    def test_retry_in_session_sets_pending_flag_when_session_id_present(self) -> None:
        controller = self._make_controller()
        state = _state_with_session()

        new_state, _, _ = controller.handle(
            state,
            "missing artifact",
            phase="development_analysis",
            agent="claude",
            retry_in_session=True,
        )

        assert new_state.session_preserve_retry_pending is True

    def test_retry_in_session_no_effect_when_session_id_absent(self) -> None:
        controller = self._make_controller()
        state = _state_without_session()

        new_state, _, _ = controller.handle(
            state,
            "missing artifact",
            phase="development_analysis",
            agent="claude",
            retry_in_session=True,
        )

        assert new_state.session_preserve_retry_pending is False

    def test_retry_in_session_false_never_sets_pending_flag(self) -> None:
        controller = self._make_controller()
        state = _state_with_session()

        new_state, _, _ = controller.handle(
            state,
            "missing artifact",
            phase="development_analysis",
            agent="claude",
            retry_in_session=False,
        )

        assert new_state.session_preserve_retry_pending is False


def _minimal_analysis_policy() -> PipelinePolicy:
    """Minimal policy routing development_analysis success to complete."""
    return PipelinePolicy(
        phases={
            "development_analysis": PhaseDefinition(
                drain="development_analysis",
                role="analysis",
                transitions=PhaseTransition(on_success="complete"),
            ),
            "complete": PhaseDefinition(
                drain="complete",
                role="terminal",
                terminal_outcome="success",
                transitions=PhaseTransition(on_success="complete"),
            ),
        },
        entry_phase="development_analysis",
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


class TestPhaseHandlerRetryInSessionFlags:
    """Phase handlers must emit retry_in_session=True for missing artifact failures."""

    def test_development_missing_planning_artifact_is_retry_in_session(self) -> None:
        from ralph.phases.execution import handle_execution_phase  # noqa: PLC0415

        effect = InvokeAgentEffect(agent_name="dev", phase="development", prompt_file="dev.txt")
        ctx = _default_policy_context()

        events = handle_execution_phase(effect, ctx)

        failure_events = [e for e in events if isinstance(e, PhaseFailureEvent)]
        assert len(failure_events) == 1
        assert failure_events[0].retry_in_session is True

    def test_development_analysis_missing_artifact_is_retry_in_session(self) -> None:
        from ralph.phases.analysis import handle_generic_analysis_phase  # noqa: PLC0415

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
        from ralph.phases.analysis import handle_generic_analysis_phase  # noqa: PLC0415

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
        from ralph.phases.review import handle_review  # noqa: PLC0415

        effect = MagicMock(spec=InvokeAgentEffect)
        ctx = MagicMock()
        ctx.workspace.exists.return_value = False

        events = handle_review(effect, ctx)

        failure_events = [e for e in events if isinstance(e, PhaseFailureEvent)]
        assert len(failure_events) == 1
        assert failure_events[0].retry_in_session is True

    def test_planning_missing_plan_artifact_is_retry_in_session(self) -> None:
        from ralph.phases.execution import handle_execution_phase  # noqa: PLC0415

        effect = InvokeAgentEffect(agent_name="planner", phase="planning", prompt_file="plan.txt")
        ctx = _default_policy_context()

        events = handle_execution_phase(effect, ctx)

        failure_events = [e for e in events if isinstance(e, PhaseFailureEvent)]
        assert len(failure_events) == 1
        assert failure_events[0].retry_in_session is True
