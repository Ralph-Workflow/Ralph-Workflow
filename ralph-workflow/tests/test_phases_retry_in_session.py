"""Tests for session-preserving retry contract through reducer and RecoveryController.

Consolidated from a previous split of 5 per-target files
(``test_phases_retry_in_session_phase_advance_clears_session_fields.py``,
``test_phases_retry_in_session_phase_failure_event_retry_in_session_flag.py``,
``test_phases_retry_in_session_phase_handler_retry_in_session_flags.py``,
``test_phases_retry_in_session_recovery_controller_session_preserving_retry.py``,
``test_phases_retry_in_session_reducer_session_preserving_retry.py``).

The split family cost one extra file-collection + import per shard on every
``make test`` invocation; merging them into a single module preserves every
existing test method (names kept stable so any external ref still resolves)
while reclaiming 4 module-level imports per shard.
"""

from __future__ import annotations

import tempfile
from functools import lru_cache
from pathlib import Path
from unittest.mock import MagicMock

from ralph.phases import PhaseContext
from ralph.phases.analysis import handle_generic_analysis_phase
from ralph.phases.execution import handle_execution_phase
from ralph.phases.review import handle_review
from ralph.pipeline.agent_retry_intent import resume_agent_retry_intent
from ralph.pipeline.effects import InvokeAgentEffect
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


# =============================================================================
# Phase advance clears session fields
# =============================================================================


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

    def test_advance_phase_clears_agent_retry_intent(self) -> None:
        state = PipelineState(
            phase="development_analysis",
            phase_chains={
                "development_analysis": AgentChainState(
                    agents=["claude"], current_index=0, retries=0
                )
            },
            last_agent_session_id="sess-x",
            agent_retry_intent=resume_agent_retry_intent("sess-x"),
        )
        policy = _minimal_analysis_policy()
        new_state, _ = reducer_reduce(state, PipelineEvent.ANALYSIS_SUCCESS, pipeline_policy=policy)
        assert new_state.agent_retry_intent.action is None

    def test_complete_clears_session_fields_on_terminal_success(self) -> None:
        state = PipelineState(
            phase="development",
            phase_chains={
                "development": AgentChainState(agents=["claude"], current_index=0, retries=0)
            },
            last_agent_session_id="sess-to-clear",
            agent_retry_intent=resume_agent_retry_intent("sess-to-clear"),
        )

        new_state, _ = reducer_reduce(
            state,
            PipelineEvent.COMPLETE,
            pipeline_policy=_terminal_transition_policy(),
        )

        assert new_state.phase == "done"
        assert new_state.last_agent_session_id is None
        assert new_state.agent_retry_intent.action is None

    def test_failed_clears_session_fields_on_terminal_failure(self) -> None:
        state = PipelineState(
            phase="development",
            phase_chains={
                "development": AgentChainState(agents=["claude"], current_index=0, retries=0)
            },
            last_agent_session_id="sess-to-clear",
            agent_retry_intent=resume_agent_retry_intent("sess-to-clear"),
            last_error="boom",
        )

        new_state, _ = reducer_reduce(
            state,
            PipelineEvent.FAILED,
            pipeline_policy=_terminal_transition_policy(),
        )

        assert new_state.phase == "failed_terminal"
        assert new_state.last_agent_session_id is None
        assert new_state.agent_retry_intent.action is None

    def test_workflow_fallback_clears_session_fields(self) -> None:
        state = PipelineState(
            phase="development",
            phase_chains={
                "development": AgentChainState(agents=["claude"], current_index=0, retries=0)
            },
            last_agent_session_id="sess-to-clear",
            agent_retry_intent=resume_agent_retry_intent("sess-to-clear"),
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
        assert new_state.agent_retry_intent.action is None


# =============================================================================
# PhaseFailureEvent retry_in_session flag
# =============================================================================


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


# =============================================================================
# Phase handler retry_in_session flags
# =============================================================================


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


# =============================================================================
# RecoveryController session-preserving retry
# =============================================================================


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


# =============================================================================
# Reducer session-preserving retry (legacy path)
# =============================================================================


class TestReducerSessionPreservingRetry:
    """Via the legacy reducer path (no RecoveryController)."""

    def test_retry_in_session_with_session_id_sets_resume_intent(self) -> None:
        state = _state_with_session()
        event = PhaseFailureEvent(
            phase="development_analysis",
            reason="missing artifact",
            recoverable=True,
            retry_in_session=True,
        )
        new_state, _ = reducer_reduce(state, event)
        assert new_state.agent_retry_intent.action == "resume"
        assert new_state.agent_retry_intent.session_id == "sess-abc123"

    def test_retry_in_session_without_session_id_leaves_no_resume_intent(self) -> None:
        state = _state_without_session()
        event = PhaseFailureEvent(
            phase="development_analysis",
            reason="missing artifact",
            recoverable=True,
            retry_in_session=True,
        )
        new_state, _ = reducer_reduce(state, event)
        assert new_state.agent_retry_intent.action is None

    def test_retry_in_session_false_never_sets_resume_intent(self) -> None:
        state = _state_with_session()
        event = PhaseFailureEvent(
            phase="development_analysis",
            reason="missing artifact",
            recoverable=True,
            retry_in_session=False,
        )
        new_state, _ = reducer_reduce(state, event)
        assert new_state.agent_retry_intent.action is None

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
