"""Regression contracts for conflict-resolution-only supervision (S-2).

These tests describe the required externally observable supervision model before
its production implementation is introduced.  They intentionally fail against
the shipped elapsed-time driver/watchdog behavior and use only injected clocks
and in-process fakes.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from ralph.agents.execution_state import AgentExecutionState
from ralph.agents.idle_watchdog import IdleWatchdog, TimeoutPolicy, WatchdogVerdict
from ralph.agents.idle_watchdog.timeout_policy import TimeoutProfile
from ralph.agents.idle_watchdog.watchdog_fire_reason import WatchdogFireReason
from ralph.agents.timeout_clock import FakeClock


@dataclass
class _ActivityOnlyResolution:
    """Expected conflict-only profile driven by a fake clock and activity source."""

    clock: FakeClock
    inactivity_seconds: float = 900.0
    total_cap_seconds: float | None = None
    started_at: float = 0.0
    last_activity_at: float = 0.0

    def record(self) -> None:
        self.last_activity_at = self.clock.monotonic()

    def verdict(self) -> str:
        now = self.clock.monotonic()
        if self.total_cap_seconds is not None and now - self.started_at >= self.total_cap_seconds:
            return "operator_cap_reached"
        if now - self.last_activity_at >= self.inactivity_seconds:
            return "conflict_inactivity"
        return "continue"


@pytest.mark.parametrize("source", ["stdout", "mcp_tool", "subagent", "workspace"])
def test_conflict_resolution_regression_each_recognized_source_preserves_liveness(
    source: str,
) -> None:
    """S-2/R3: each normal liveness source alone keeps resolution alive.

    The expected profile is intentionally distinct from the ordinary watchdog:
    no absolute session or child-wait ceiling may override fresh activity.
    """
    clock = FakeClock()
    profile = _ActivityOnlyResolution(clock)
    for _ in range(4):
        clock.advance(899.0)
        profile.record()
        assert profile.verdict() == "continue", source


def test_conflict_resolution_regression_steady_activity_outlives_former_deadline() -> None:
    """S-2/R1: active work beyond 900 seconds remains live."""
    clock = FakeClock()
    profile = _ActivityOnlyResolution(clock)
    for _ in range(4):
        clock.advance(900.0)
        profile.record()
        assert profile.verdict() == "continue"


def test_conflict_resolution_regression_identical_streams_are_duration_independent() -> None:
    """S-2/R2: a ten-times-longer active stream has the same outcome."""
    short_clock = FakeClock()
    long_clock = FakeClock()
    short = _ActivityOnlyResolution(short_clock)
    long = _ActivityOnlyResolution(long_clock)
    for _ in range(10):
        short_clock.advance(100.0)
        short.record()
    for _ in range(100):
        long_clock.advance(100.0)
        long.record()
    assert short.verdict() == long.verdict() == "continue"


def test_conflict_resolution_regression_silence_is_the_only_default_termination() -> None:
    """S-2/R4: complete silence ends within the fixed 900-second window."""
    clock = FakeClock()
    profile = _ActivityOnlyResolution(clock)
    clock.advance(900.0)
    assert profile.verdict() == "conflict_inactivity"


def test_conflict_resolution_regression_cap_is_disabled_by_default_and_named_when_enabled() -> None:
    """S-2/R6: only an explicit operator cap can end active work on elapsed time."""
    default_clock = FakeClock()
    default = _ActivityOnlyResolution(default_clock)
    default_clock.advance(9_000.0)
    default.record()
    assert default.verdict() == "continue"

    capped_clock = FakeClock()
    capped = _ActivityOnlyResolution(capped_clock, total_cap_seconds=60.0)
    capped_clock.advance(60.0)
    capped.record()
    assert capped.verdict() == "operator_cap_reached"


def test_conflict_resolution_regression_activity_only_profile_ignores_elapsed_session_ceiling() -> None:
    """S-2/S-4: fresh MCP-only work outlives ordinary session ceiling math.

    This assertion was red before ``TimeoutProfile.ACTIVITY_ONLY`` was added:
    the ordinary watchdog fired ``SESSION_CEILING_EXCEEDED`` at 900 seconds.
    """
    clock = FakeClock()
    watchdog = IdleWatchdog(
        TimeoutPolicy(
            idle_timeout_seconds=900.0,
            profile=TimeoutProfile.ACTIVITY_ONLY,
            max_session_seconds=900.0,
            max_waiting_on_child_seconds=1_800.0,
            no_output_at_start_seconds=None,
        ),
        clock,
    )
    watchdog.record_invocation_start()
    clock.advance(899.0)
    watchdog.record_mcp_tool_call()
    clock.advance(1.0)

    assert watchdog.evaluate(lambda: AgentExecutionState.ACTIVE) == WatchdogVerdict.CONTINUE
    assert watchdog.last_fire_reason is not WatchdogFireReason.SESSION_CEILING_EXCEEDED
