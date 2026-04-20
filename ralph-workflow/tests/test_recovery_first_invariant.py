"""Regression tests for the recover-first invariant.

These tests verify that when a phase handler or agent raises an exception,
the pipeline converts it to PhaseFailureEvent(recoverable=True) and routes
through the retry/fallback chain before reaching PHASE_FAILED.

This file was added as part of the fix for the bug where the pipeline
failed with 'Unknown failure' bypassing the retry/fallback chain.
"""

from __future__ import annotations

from typing import cast

import pytest

from ralph.config.enums import PHASE_DEVELOPMENT, PHASE_FAILED
from ralph.pipeline.effects import ExitFailureEffect
from ralph.pipeline.events import PhaseFailureEvent
from ralph.pipeline.reducer import reduce as reducer_reduce
from ralph.pipeline.state import AgentChainState, PipelineState

# Expected number of retries when a phase fails with a 2-agent chain:
# 3 retries for agent 0 + 3 retries for agent 1 = 6 total retries
_EXPECTED_TOTAL_RETRIES = 6


def _reduce(
    state: PipelineState,
    event: object,
) -> tuple[PipelineState, list[object]]:
    return reducer_reduce(state, cast("object", event), None)


class TestRecoveryFirstBehavior:
    """Regression tests for the recover-first invariant."""

    def test_phase_handler_crash_exhausts_retries_then_fallbacks(self) -> None:
        """A phase that always fails must exhaust retries and fallbacks before PHASE_FAILED.

        This is the single most important regression guard for the bug where
        the pipeline exited on the first exception instead of walking the full
        retry/fallback chain.
        """
        # State with a 2-agent dev_chain
        state = PipelineState(
            phase=PHASE_DEVELOPMENT,
            dev_chain=AgentChainState(agents=["claude", "opencode"], current_index=0, retries=0),
        )

        # PhaseFailureEvent that simulates a handler crash
        crash_event = PhaseFailureEvent(
            phase="development",
            reason="Phase handler crashed: RuntimeError: boom",
            recoverable=True,
        )

        # Agent 0: 3 retries (retries 0->1->2->3)
        for expected_retries in range(1, 4):
            state, effects = _reduce(state, crash_event)
            assert state.phase == PHASE_DEVELOPMENT
            assert state.dev_chain.current_index == 0
            assert state.dev_chain.retries == expected_retries
            assert effects == []

        # 4th crash on agent 0: fallback to agent 1 (retries reset to 0)
        state, effects = _reduce(state, crash_event)
        assert state.phase == PHASE_DEVELOPMENT
        assert state.dev_chain.current_index == 1
        assert state.dev_chain.retries == 0
        assert state.metrics.total_fallbacks == 1
        assert effects == []

        # Agent 1: 3 more retries (retries 0->1->2->3)
        for expected_retries in range(1, 4):
            state, effects = _reduce(state, crash_event)
            assert state.phase == PHASE_DEVELOPMENT
            assert state.dev_chain.current_index == 1
            assert state.dev_chain.retries == expected_retries
            assert effects == []

        # Final crash on agent 1 (chain exhausted): PHASE_FAILED with descriptive reason
        state, effects = _reduce(state, crash_event)
        assert state.phase == PHASE_FAILED
        assert "Phase handler crashed: RuntimeError: boom" in state.last_error
        assert state.last_error != "Unknown failure"
        assert state.metrics.total_retries == _EXPECTED_TOTAL_RETRIES
        assert state.metrics.total_fallbacks == 1
        assert len(effects) == 1
        effect = effects[0]
        assert isinstance(effect, ExitFailureEffect)
        assert effect.reason == state.last_error
        assert "Phase handler crashed" in effect.reason
        assert effect.reason != "Unknown failure"

    def test_phase_failure_recoverable_empty_reason_produces_descriptive_error(
        self,
    ) -> None:
        """PhaseFailureEvent with empty reason must still produce a descriptive last_error."""
        state = PipelineState(
            phase=PHASE_DEVELOPMENT,
            dev_chain=AgentChainState(agents=["claude"], current_index=0, retries=3),
        )
        event = PhaseFailureEvent(phase="development", reason="", recoverable=True)

        new_state, effects = _reduce(state, event)

        assert new_state.phase == PHASE_FAILED
        assert new_state.last_error is not None
        assert new_state.last_error != ""
        assert new_state.last_error != "Unknown failure"
        assert "development" in new_state.last_error
        assert len(effects) == 1
        assert isinstance(effects[0], ExitFailureEffect)
        assert effects[0].reason != ""
        assert effects[0].reason != "Unknown failure"

    def test_phase_failure_recoverable_whitespace_reason_produces_descriptive_error(
        self,
    ) -> None:
        """Whitespace-only reason must still produce a descriptive last_error."""
        state = PipelineState(
            phase=PHASE_DEVELOPMENT,
            dev_chain=AgentChainState(agents=["claude"], current_index=0, retries=3),
        )
        event = PhaseFailureEvent(phase="development", reason="   ", recoverable=True)

        new_state, effects = _reduce(state, event)

        assert new_state.phase == PHASE_FAILED
        assert new_state.last_error is not None
        assert new_state.last_error.strip() != ""
        assert new_state.last_error != "Unknown failure"
        assert "development" in new_state.last_error
        assert len(effects) == 1
        assert isinstance(effects[0], ExitFailureEffect)
        assert effects[0].reason.strip() != ""
        assert effects[0].reason != "Unknown failure"

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
        phase=PHASE_DEVELOPMENT,
        dev_chain=AgentChainState(agents=["claude"], current_index=0, retries=3),
    )
    event = PhaseFailureEvent(phase="development", reason="", recoverable=True)

    new_state, effects = _reduce(state, event)

    assert new_state.phase == PHASE_FAILED
    assert new_state.last_error is not None
    assert new_state.last_error != ""
    assert new_state.last_error != "Unknown failure"
    assert "development" in new_state.last_error
    assert len(effects) == 1
    assert isinstance(effects[0], ExitFailureEffect)
    assert effects[0].reason != ""
    assert effects[0].reason != "Unknown failure"
    assert "development" in effects[0].reason


def test_phase_failure_recoverable_whitespace_reason_produces_descriptive_error() -> None:
    """Whitespace-only reason must still produce a descriptive last_error."""
    state = PipelineState(
        phase=PHASE_DEVELOPMENT,
        dev_chain=AgentChainState(agents=["claude"], current_index=0, retries=3),
    )
    event = PhaseFailureEvent(phase="development", reason="   ", recoverable=True)

    new_state, effects = _reduce(state, event)

    assert new_state.phase == PHASE_FAILED
    assert new_state.last_error is not None
    assert new_state.last_error.strip() != ""
    assert new_state.last_error != "Unknown failure"
    assert "development" in new_state.last_error
    assert len(effects) == 1
    assert isinstance(effects[0], ExitFailureEffect)
    assert effects[0].reason.strip() != ""
    assert effects[0].reason != "Unknown failure"


def test_phase_failure_not_recoverable_empty_reason_produces_descriptive_error() -> None:
    """PhaseFailureEvent(recoverable=False) with empty reason produces descriptive error."""
    state = PipelineState(
        phase=PHASE_DEVELOPMENT,
        dev_chain=AgentChainState(agents=["claude"], current_index=0, retries=0),
    )
    event = PhaseFailureEvent(phase="development_analysis", reason="", recoverable=False)

    new_state, effects = _reduce(state, event)

    assert new_state.phase == PHASE_FAILED
    assert new_state.last_error is not None
    assert new_state.last_error != ""
    assert new_state.last_error != "Unknown failure"
    assert "development_analysis" in new_state.last_error
    assert len(effects) == 1
    assert isinstance(effects[0], ExitFailureEffect)
    assert effects[0].reason != ""
    assert effects[0].reason != "Unknown failure"
