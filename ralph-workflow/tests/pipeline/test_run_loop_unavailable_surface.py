"""Black-box tests for run_loop operator-visible surface for unavailable agents."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock

from ralph.pipeline.run_loop import _LoopContext
from ralph.pipeline.state import AgentChainState, PipelineState
from ralph.recovery.events import FailureEvent, FalloverEvent


class TestRunLoopUnavailableSurface:
    """Tests for unavailability_reason surfacing in run_loop display and logs."""

    def test_waiting_line_includes_unavailability_reason(self) -> None:
        ctx = MagicMock(spec=_LoopContext)
        ctx.active_display = MagicMock()
        ctx.last_waiting_state_phase = None

        state = PipelineState(
            phase="development",
            phase_chains={
                "development": AgentChainState(
                    agents=["claude", "opencode"],
                    current_index=0,
                    retries=0,
                )
            },
        ).copy_with(
            last_error=(
                "all agents unavailable (last reason: out_of_credits);"
                " waiting for cooldown expiry"
            ),
            last_retry_delay_ms=30_000,
            last_unavailability_reason="out_of_credits",
        )
        assert state.last_unavailability_reason == "out_of_credits"

    def test_failure_event_carries_unavailability_reason_string(self) -> None:
        evt = FailureEvent(
            timestamp=datetime.now(UTC),
            phase="development",
            agent="claude",
            category="agent",
            reason="Agent fault: out of credits",
            counted_against_budget=True,
            chain_capacity_remaining=1,
            recovery_cycle=0,
            retry_delay_ms=0,
            watchdog_reason="no_output_at_start",
            unavailability_reason="out_of_credits",
        )
        assert evt.unavailability_reason == "out_of_credits"

    def test_fallover_event_carries_unavailability_reason_string(self) -> None:
        evt = FalloverEvent.now(
            phase="development",
            from_agent="claude",
            to_agent="opencode",
            reason="Agent unavailable",
            watchdog_reason="no_progress_quiet",
            unavailability_reason="stale_child_quiet",
        )
        assert evt.unavailability_reason == "stale_child_quiet"

    def test_state_carries_last_unavailability_reason(self) -> None:
        state = PipelineState(
            phase="development",
            phase_chains={
                "development": AgentChainState(
                    agents=["claude"],
                    current_index=0,
                    retries=0,
                )
            },
        ).copy_with(last_unavailability_reason="out_of_credits")

        assert state.last_unavailability_reason == "out_of_credits"

    def test_state_last_unavailability_reason_defaults_to_none(self) -> None:
        state = PipelineState(
            phase="development",
            phase_chains={
                "development": AgentChainState(
                    agents=["claude"],
                    current_index=0,
                    retries=0,
                )
            },
        )
        assert state.last_unavailability_reason is None
