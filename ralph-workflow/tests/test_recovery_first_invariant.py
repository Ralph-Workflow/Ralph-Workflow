from __future__ import annotations

import pytest

from ralph.pipeline.effects import Effect, ExitFailureEffect
from ralph.pipeline.events import PhaseFailureEvent, PipelineEvent
from ralph.pipeline.reducer import reduce as reducer_reduce
from ralph.pipeline.state import AgentChainState, PipelineState
from ralph.policy.models import PhaseDefinition, PhaseTransition, PipelinePolicy
from ralph.recovery.controller import RecoveryController, RecoveryControllerOptions

_EXPECTED_TOTAL_RETRIES = 5


def _minimal_policy() -> PipelinePolicy:
    return PipelinePolicy(
        phases={
            "development": PhaseDefinition(
                drain="development",
                transitions=PhaseTransition(
                    on_success="complete",
                    on_failure=None,
                    on_loopback="development",
                ),
            ),
        },
        entry_phase="development",
        terminal_phase="complete",
    )


def _reduce(
    state: PipelineState,
    event: PhaseFailureEvent,
    controller: RecoveryController | None = None,
) -> tuple[PipelineState, list[Effect]]:
    return reducer_reduce(state, event, _minimal_policy(), recovery=controller)


def _chain_for_phase(state: PipelineState, phase: str) -> AgentChainState:
    chain = state.chain_for_phase(phase)
    assert chain is not None
    return chain


class TestRecoveryFirstBehavior:
    def test_phase_handler_crash_walks_the_owner_decided_chain_before_stopping(self) -> None:
        state = PipelineState(
            phase="development",
            phase_chains={
                "development": AgentChainState(
                    agents=["claude", "opencode"], current_index=0, retries=0
                )
            },
        )

        crash_event = PhaseFailureEvent(
            phase="development",
            reason="Phase handler crashed: RuntimeError: boom",
            recoverable=True,
        )
        controller = RecoveryController(options=RecoveryControllerOptions(cycle_cap=10))

        for expected_retries in range(1, 3):
            state, effects = _reduce(state, crash_event, controller)
            chain = _chain_for_phase(state, "development")
            assert state.phase == "development"
            assert chain.current_index == 0
            assert chain.retries == expected_retries
            assert effects == []

        state, effects = _reduce(state, crash_event, controller)
        chain = _chain_for_phase(state, "development")
        assert state.phase == "development"
        assert chain.current_index == 1
        assert chain.retries == 0
        assert effects == []

        for expected_retries in range(1, 3):
            state, effects = _reduce(state, crash_event, controller)
            chain = _chain_for_phase(state, "development")
            assert state.phase == "development"
            assert chain.current_index == 1
            assert chain.retries == expected_retries
            assert effects == []

        state, effects = _reduce(state, crash_event, controller)
        assert state.phase == "development"
        assert state.is_waiting_state is True
        assert state.last_retry_delay_ms > 0
        assert state.last_error is not None
        assert state.last_error != "Unknown failure"
        assert effects == []

    def test_agent_failure_walk_counts_the_owner_decided_retries_and_fallback(self) -> None:
        state = PipelineState(
            phase="development",
            phase_chains={
                "development": AgentChainState(
                    agents=["claude", "opencode"], current_index=0, retries=0
                )
            },
        )
        controller = RecoveryController(options=RecoveryControllerOptions(cycle_cap=10))

        for _ in range(3):
            state, effects = reducer_reduce(
                state,
                PipelineEvent.AGENT_FAILURE,
                _minimal_policy(),
                recovery=controller,
            )
            assert effects == []

        chain = _chain_for_phase(state, "development")
        assert chain.current_index == 1
        assert chain.retries == 0
        assert state.metrics.total_fallbacks == 1
        assert state.metrics.total_retries == 2
        assert state.metrics.total_retries < _EXPECTED_TOTAL_RETRIES

    def test_phase_failure_recoverable_empty_reason_produces_descriptive_error(
        self,
    ) -> None:
        state = PipelineState(
            phase="development",
            phase_chains={
                "development": AgentChainState(agents=["claude"], current_index=0, retries=3)
            },
        )
        event = PhaseFailureEvent(phase="development", reason="", recoverable=True)

        new_state, effects = _reduce(state, event)

        assert new_state.phase == "failed_terminal"
        assert new_state.last_error is not None
        assert new_state.last_error != ""
        assert new_state.last_error != "Unknown failure"
        assert "development" in new_state.last_error
        assert new_state.recovery_epoch == 1
        assert effects == []

    def test_phase_failure_recoverable_whitespace_reason_produces_descriptive_error(
        self,
    ) -> None:
        state = PipelineState(
            phase="development",
            phase_chains={
                "development": AgentChainState(agents=["claude"], current_index=0, retries=3)
            },
        )
        event = PhaseFailureEvent(phase="development", reason="   ", recoverable=True)

        new_state, effects = _reduce(state, event)

        assert new_state.phase == "failed_terminal"
        assert new_state.last_error is not None
        assert new_state.last_error.strip() != ""
        assert new_state.last_error != "Unknown failure"
        assert "development" in new_state.last_error
        assert new_state.recovery_epoch == 1
        assert effects == []

    def test_exit_failure_effect_rejects_whitespace_only_reason(self) -> None:
        with pytest.raises(ValueError, match="descriptive"):
            ExitFailureEffect(reason="   ")

    def test_exit_failure_effect_rejects_empty_reason(self) -> None:
        with pytest.raises(ValueError, match="descriptive"):
            ExitFailureEffect(reason="")

    def test_exit_failure_effect_rejects_forbidden_sentinels(self) -> None:
        for sentinel in ("Unknown failure", "unknown failure", "None", "null"):
            with pytest.raises(ValueError, match="sentinel"):
                ExitFailureEffect(reason=sentinel)

    def test_exit_failure_effect_rejects_sentinel_as_substring(self) -> None:
        for reason in (
            "development: Unknown failure",
            "review: unknown failure",
            "analysis: None",
            "fix: null",
            "Unknown failure occurred",
            "unknown failure in development",
        ):
            with pytest.raises(ValueError, match="sentinel"):
                ExitFailureEffect(reason=reason)

    def test_exit_failure_effect_accepts_valid_descriptive_reasons(self) -> None:
        valid_reasons = [
            "development: Phase handler crashed: RuntimeError: boom",
            "review: Missing issues artifact",
            "analysis: Invalid decision artifact",
            "Commit failed: git push rejected",
            "Agent invocation failed: connection timeout",
        ]
        for reason in valid_reasons:
            effect = ExitFailureEffect(reason=reason)
            assert effect.reason == reason


def test_phase_failure_recoverable_empty_reason_produces_descriptive_error() -> None:
    state = PipelineState(
        phase="development",
        phase_chains={
            "development": AgentChainState(agents=["claude"], current_index=0, retries=3)
        },
    )
    event = PhaseFailureEvent(phase="development", reason="", recoverable=True)

    new_state, effects = _reduce(state, event)

    assert new_state.phase == "failed_terminal"
    assert new_state.last_error is not None
    assert new_state.last_error != ""
    assert new_state.last_error != "Unknown failure"
    assert "development" in new_state.last_error
    assert new_state.recovery_epoch == 1
    assert effects == []


def test_phase_failure_recoverable_whitespace_reason_produces_descriptive_error() -> None:
    state = PipelineState(
        phase="development",
        phase_chains={
            "development": AgentChainState(agents=["claude"], current_index=0, retries=3)
        },
    )
    event = PhaseFailureEvent(phase="development", reason="   ", recoverable=True)

    new_state, effects = _reduce(state, event)

    assert new_state.phase == "failed_terminal"
    assert new_state.last_error is not None
    assert new_state.last_error.strip() != ""
    assert new_state.last_error != "Unknown failure"
    assert "development" in new_state.last_error
    assert new_state.recovery_epoch == 1
    assert effects == []


def test_phase_failure_not_recoverable_empty_reason_produces_descriptive_error() -> None:
    state = PipelineState(phase="development")
    event = PhaseFailureEvent(phase="development_analysis", reason="", recoverable=False)

    new_state, effects = _reduce(state, event)

    assert new_state.phase == "failed_terminal"
    assert new_state.last_error is not None
    assert new_state.last_error != ""
    assert new_state.last_error != "Unknown failure"
    assert "development_analysis" in new_state.last_error
    assert new_state.recovery_epoch == 1
    assert effects == []
