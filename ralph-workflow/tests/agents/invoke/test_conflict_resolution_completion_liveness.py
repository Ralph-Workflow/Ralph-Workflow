"""Completion-path liveness regressions for conflict resolution (S-2)."""

from __future__ import annotations

from ralph.agents.execution_state import AgentExecutionState
from ralph.agents.idle_watchdog import IdleWatchdog, TimeoutPolicy, WatchdogVerdict
from ralph.agents.idle_watchdog.timeout_policy import TimeoutProfile
from ralph.agents.timeout_clock import FakeClock


def test_conflict_resolution_regression_parent_exit_keeps_scoped_activity_supervised() -> None:
    """S-2/R3: parent exit cannot discard fresh scoped MCP activity as idle."""
    clock = FakeClock()
    watchdog = IdleWatchdog(
        TimeoutPolicy(
            idle_timeout_seconds=900.0,
            profile=TimeoutProfile.ACTIVITY_ONLY,
            process_exit_wait_seconds=0.0,
            descendant_wait_timeout_seconds=0.0,
        ),
        clock,
    )
    watchdog.record_invocation_start()
    clock.advance(899.0)
    watchdog.record_mcp_tool_call()

    assert watchdog.evaluate(lambda: AgentExecutionState.WAITING_ON_CHILD) is WatchdogVerdict.CONTINUE
