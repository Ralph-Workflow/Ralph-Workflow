"""Tests for session-preserving retry contract through reducer and RecoveryController."""

from __future__ import annotations

from unittest.mock import MagicMock

from ralph.pipeline.effects import InvokeAgentEffect
from ralph.pipeline.events import PhaseFailureEvent, PipelineEvent
from ralph.pipeline.reducer import reduce as reducer_reduce
from ralph.pipeline.state import AgentChainState, PipelineState
from ralph.recovery.controller import RecoveryController


def _state_with_session(phase: str = "development_analysis") -> PipelineState:
    return PipelineState(
        phase=phase,
        dev_chain=AgentChainState(agents=["claude"], current_index=0, retries=0),
        last_agent_session_id="sess-abc123",
    )


def _state_without_session(phase: str = "development_analysis") -> PipelineState:
    return PipelineState(
        phase=phase,
        dev_chain=AgentChainState(agents=["claude"], current_index=0, retries=0),
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


class TestPhaseAdvanceClearsSessionFields:
    """Phase advance must clear session fields to prevent cross-phase session leaks."""

    def test_advance_phase_clears_last_agent_session_id(self) -> None:
        state = PipelineState(
            phase="development_analysis",
            dev_chain=AgentChainState(agents=["claude"], current_index=0, retries=0),
            last_agent_session_id="sess-to-clear",
        )
        new_state, _ = reducer_reduce(state, PipelineEvent.ANALYSIS_SUCCESS, pipeline_policy=None)
        assert new_state.last_agent_session_id is None

    def test_advance_phase_clears_session_preserve_retry_pending(self) -> None:
        state = PipelineState(
            phase="development_analysis",
            dev_chain=AgentChainState(agents=["claude"], current_index=0, retries=0),
            last_agent_session_id="sess-x",
            session_preserve_retry_pending=True,
        )
        new_state, _ = reducer_reduce(state, PipelineEvent.ANALYSIS_SUCCESS, pipeline_policy=None)
        assert new_state.session_preserve_retry_pending is False


class TestPhaseHandlerRetryInSessionFlags:
    """Phase handlers must emit retry_in_session=True for missing artifact failures."""

    def test_development_missing_planning_artifact_is_retry_in_session(self) -> None:
        from ralph.phases.development import handle_development  # noqa: PLC0415

        effect = MagicMock(spec=InvokeAgentEffect)
        ctx = MagicMock()
        ctx.workspace.exists.return_value = False

        events = handle_development(effect, ctx)

        failure_events = [e for e in events if isinstance(e, PhaseFailureEvent)]
        assert len(failure_events) == 1
        assert failure_events[0].retry_in_session is True

    def test_development_analysis_missing_artifact_is_retry_in_session(self) -> None:
        from ralph.phases.development import handle_development_analysis  # noqa: PLC0415

        effect = MagicMock(spec=InvokeAgentEffect)
        ctx = MagicMock()
        ctx.workspace.exists.return_value = False

        events = handle_development_analysis(effect, ctx)

        failure_events = [e for e in events if isinstance(e, PhaseFailureEvent)]
        assert len(failure_events) == 1
        assert failure_events[0].retry_in_session is True

    def test_review_analysis_missing_artifact_is_retry_in_session(self) -> None:
        from ralph.phases.review import handle_review_analysis  # noqa: PLC0415

        effect = MagicMock(spec=InvokeAgentEffect)
        ctx = MagicMock()
        ctx.workspace.exists.return_value = False

        events = handle_review_analysis(effect, ctx)

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
        from ralph.phases.planning import handle_planning  # noqa: PLC0415

        effect = MagicMock(spec=InvokeAgentEffect)
        ctx = MagicMock()
        ctx.workspace.exists.return_value = False

        events = handle_planning(effect, ctx)

        failure_events = [e for e in events if isinstance(e, PhaseFailureEvent)]
        assert len(failure_events) == 1
        assert failure_events[0].retry_in_session is True

    def test_fix_missing_fix_result_artifact_is_retry_in_session(self) -> None:
        from ralph.phases.fix import handle_fix  # noqa: PLC0415

        effect = MagicMock(spec=InvokeAgentEffect)
        ctx = MagicMock()
        ctx.workspace.exists.return_value = False

        events = handle_fix(effect, ctx)

        failure_events = [e for e in events if isinstance(e, PhaseFailureEvent)]
        assert len(failure_events) == 1
        assert failure_events[0].retry_in_session is True
