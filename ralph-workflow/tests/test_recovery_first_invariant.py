"""Regression tests for the recover-first invariant.

These tests verify that when a phase handler or agent raises an exception,
the pipeline converts it to PhaseFailureEvent(recoverable=True) and routes
through the retry/fallback chain before reaching "failed".

This file was added as part of the fix for the bug where the pipeline
failed with 'Unknown failure' bypassing the retry/fallback chain.
"""

from __future__ import annotations

import pytest

from ralph.pipeline.effects import Effect, ExitFailureEffect
from ralph.pipeline.events import PhaseFailureEvent, PipelineEvent
from ralph.pipeline.reducer import reduce as reducer_reduce
from ralph.pipeline.state import AgentChainState, PipelineState
from ralph.policy.models import PhaseDefinition, PhaseTransition, PipelinePolicy
from ralph.recovery.controller import RecoveryController, RecoveryControllerOptions

# Expected number of retries when a phase fails with a 2-agent chain:
# 2 retries for agent 0 (the third attempt demotes it) + 3 retries for
# agent 1 = 5 total retries. The bound comes from the owner (the recovery
# controller and its failure ledger), never from a local counter.
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
    """Regression tests for the recover-first invariant."""

    def test_phase_handler_crash_walks_the_owner_decided_chain_before_stopping(self) -> None:
        """A phase that always fails walks the chain on the owner's verdict.

        This is the single most important regression guard for the bug where
        the pipeline exited on the first exception instead of walking the full
        retry/fallback chain. The walk is decided by the single owner (the
        recovery controller and its failure ledger): agent 0 is retried while
        it stays eligible, demoted into cooldown after three unproductive
        attempts, and the chain falls over to agent 1. When every agent is
        cooling down the run WAITS for the cooldown instead of exiting.
        """
        # State with a 2-agent phase chain
        state = PipelineState(
            phase="development",
            phase_chains={
                "development": AgentChainState(
                    agents=["claude", "opencode"], current_index=0, retries=0
                )
            },
        )

        # PhaseFailureEvent that simulates a handler crash
        crash_event = PhaseFailureEvent(
            phase="development",
            reason="Phase handler crashed: RuntimeError: boom",
            recoverable=True,
        )
        controller = RecoveryController(options=RecoveryControllerOptions(cycle_cap=10))

        # Agent 0 is retried while the owner keeps it eligible. Its third
        # unproductive attempt is the one the owner demotes (cooldown), so the
        # walk leaves it after two retries.
        for expected_retries in range(1, 3):
            state, effects = _reduce(state, crash_event, controller)
            chain = _chain_for_phase(state, "development")
            assert state.phase == "development"
            assert chain.current_index == 0
            assert chain.retries == expected_retries
            assert effects == []

        # 3rd crash on agent 0: the owner demotes it and falls over to agent 1
        state, effects = _reduce(state, crash_event, controller)
        chain = _chain_for_phase(state, "development")
        assert state.phase == "development"
        assert chain.current_index == 1
        assert chain.retries == 0
        assert effects == []

        # Agent 1: 2 more retries (retries 0->1->2); its third attempt is
        # the one the owner demotes.
        for expected_retries in range(1, 3):
            state, effects = _reduce(state, crash_event, controller)
            chain = _chain_for_phase(state, "development")
            assert state.phase == "development"
            assert chain.current_index == 1
            assert chain.retries == expected_retries
            assert effects == []

        # Final crash on agent 1: every agent is cooling down, so the run
        # WAITS for the earliest cooldown instead of exiting the pipeline.
        state, effects = _reduce(state, crash_event, controller)
        assert state.phase == "development"
        assert state.is_waiting_state is True
        assert state.last_retry_delay_ms > 0
        assert state.last_error is not None
        assert state.last_error != "Unknown failure"
        assert effects == []

    def test_agent_failure_walk_counts_the_owner_decided_retries_and_fallback(self) -> None:
        """The reducer's agent-failure route books the owner's walk.

        The retry/fallback counters are still reported, but every step of the
        walk is the owner's verdict: agent 0 is retried twice, then demoted
        into cooldown, and the chain falls over to agent 1.
        """
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
        """PhaseFailureEvent with empty reason must still produce a descriptive last_error."""
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
        """Whitespace-only reason must still produce a descriptive last_error."""
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
        """ExitFailureEffect must reject whitespace-only reasons with ValueError."""
        with pytest.raises(ValueError, match="descriptive"):
            ExitFailureEffect(reason="   ")

    def test_exit_failure_effect_rejects_empty_reason(self) -> None:
        """ExitFailureEffect must reject empty string reasons with ValueError."""
        with pytest.raises(ValueError, match="descriptive"):
            ExitFailureEffect(reason="")

    def test_exit_failure_effect_rejects_forbidden_sentinels(self) -> None:
        """ExitFailureEffect must reject known sentinel strings as exact matches."""
        for sentinel in ("Unknown failure", "unknown failure", "None", "null"):
            with pytest.raises(ValueError, match="sentinel"):
                ExitFailureEffect(reason=sentinel)

    def test_exit_failure_effect_rejects_sentinel_as_substring(self) -> None:
        """ExitFailureEffect must reject reasons containing sentinels as substrings.

        This prevents bugs where a descriptive message is constructed by
        concatenating a phase name with a sentinel reason, e.g.
        'development: Unknown failure'.
        """
        # These reasons contain a forbidden sentinel as a substring and must be rejected
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
        """ExitFailureEffect must accept reasons that are descriptive and contain no sentinels."""
        valid_reasons = [
            "development: Phase handler crashed: RuntimeError: boom",
            "review: Missing issues artifact",
            "analysis: Invalid decision artifact",
            "Commit failed: git push rejected",
            "Agent invocation failed: connection timeout",
        ]
        for reason in valid_reasons:
            # Should not raise
            effect = ExitFailureEffect(reason=reason)
            assert effect.reason == reason


def test_phase_failure_recoverable_empty_reason_produces_descriptive_error() -> None:
    """PhaseFailureEvent with empty reason must still produce a descriptive last_error."""
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
    """Whitespace-only reason must still produce a descriptive last_error."""
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
    """PhaseFailureEvent(recoverable=False) with empty reason produces descriptive error."""
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
