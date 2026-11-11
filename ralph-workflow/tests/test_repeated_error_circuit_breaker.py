from __future__ import annotations

import json

from ralph.agents.activity import AgentActivityKind
from ralph.agents.execution_state import AgentExecutionState
from ralph.agents.execution_state.opencode_execution_strategy import OpenCodeExecutionStrategy
from ralph.agents.idle_watchdog import (
    IdleWatchdog,
    TimeoutPolicy,
    WatchdogFireReason,
    WatchdogVerdict,
)
from ralph.agents.timeout_clock import FakeClock


def _policy(
    *,
    idle_timeout_seconds: float | None = 300.0,
    consecutive: int | None = 5,
    window_count: int | None = 8,
    window_seconds: float | None = 600.0,
) -> TimeoutPolicy:
    return TimeoutPolicy(
        idle_timeout_seconds=idle_timeout_seconds,
        drain_window_seconds=0.0,
        max_session_seconds=None,
        repeated_error_consecutive_threshold=consecutive,
        repeated_error_window_count=window_count,
        repeated_error_window_seconds=window_seconds,
    )


def _evaluate(watchdog: IdleWatchdog) -> WatchdogVerdict:
    return watchdog.evaluate(classify_quiet=lambda: AgentExecutionState.RESUMABLE_CONTINUE)


_TIMEOUT_ERROR = "MCP error -32001: Request timed out"


def test_consecutive_identical_errors_fire_repeated_error_loop() -> None:
    clock = FakeClock()
    watchdog = IdleWatchdog(_policy(consecutive=5), clock)
    for _ in range(4):
        watchdog.record_error_activity(_TIMEOUT_ERROR)
        clock.advance(34.0)
        assert _evaluate(watchdog) == WatchdogVerdict.CONTINUE
    watchdog.record_error_activity(_TIMEOUT_ERROR)
    assert _evaluate(watchdog) == WatchdogVerdict.FIRE
    assert watchdog.last_fire_reason == WatchdogFireReason.REPEATED_ERROR_LOOP


def test_real_output_between_errors_prevents_consecutive_repeated_error_fire() -> None:
    clock = FakeClock()
    watchdog = IdleWatchdog(
        _policy(consecutive=5, window_count=None, window_seconds=None),
        clock,
    )
    for _ in range(10):
        watchdog.record_error_activity(_TIMEOUT_ERROR)
        clock.advance(10.0)
        watchdog.record_activity()
        clock.advance(10.0)
        assert _evaluate(watchdog) == WatchdogVerdict.CONTINUE


def test_sustained_error_rate_fires_even_when_output_interleaves() -> None:
    clock = FakeClock()
    watchdog = IdleWatchdog(
        _policy(consecutive=None, window_count=8, window_seconds=600.0),
        clock,
    )
    for _ in range(7):
        watchdog.record_error_activity(_TIMEOUT_ERROR)
        watchdog.record_activity()
        clock.advance(20.0)
        assert _evaluate(watchdog) == WatchdogVerdict.CONTINUE
    watchdog.record_error_activity(_TIMEOUT_ERROR)

    assert _evaluate(watchdog) == WatchdogVerdict.FIRE
    assert watchdog.last_fire_reason == WatchdogFireReason.REPEATED_ERROR_LOOP


def test_error_lines_do_not_reset_the_idle_deadline() -> None:
    clock = FakeClock()
    watchdog = IdleWatchdog(
        _policy(idle_timeout_seconds=300.0, consecutive=None, window_count=None),
        clock,
    )
    watchdog.record_error_activity(_TIMEOUT_ERROR)
    clock.advance(301.0)
    assert _evaluate(watchdog) == WatchdogVerdict.FIRE
    assert watchdog.last_fire_reason == WatchdogFireReason.NO_OUTPUT_DEADLINE


def test_opencode_toplevel_error_line_classified_as_error_line() -> None:
    strategy = OpenCodeExecutionStrategy()
    line = json.dumps({"type": "error", "error": {"message": _TIMEOUT_ERROR}})
    signal = strategy.classify_activity_line(line)
    assert signal is not None
    assert signal.kind == AgentActivityKind.ERROR_LINE
    assert "32001" in signal.raw


def test_opencode_tool_state_error_regression_preserves_call_and_error_evidence() -> None:
    strategy = OpenCodeExecutionStrategy()
    line = json.dumps(
        {
            "type": "tool_use",
            "part": {
                "tool": "exec",
                "state": {"status": "error", "error": _TIMEOUT_ERROR},
            },
        }
    )
    signal = strategy.classify_activity_line(line)
    assert signal is not None
    assert signal.kind == AgentActivityKind.TOOL_USE
    assert signal.error_message is not None
    assert "32001" in signal.error_message


def test_opencode_tool_state_error_regression_fires_repeated_error_loop() -> None:
    clock = FakeClock()
    strategy = OpenCodeExecutionStrategy()
    watchdog = IdleWatchdog(_policy(consecutive=5), clock)
    line = json.dumps(
        {
            "type": "tool_use",
            "part": {"tool": "exec", "state": {"status": "error", "error": _TIMEOUT_ERROR}},
        }
    )
    fired = False
    for _ in range(6):
        signal = strategy.classify_activity_line(line)
        assert signal is not None
        assert signal.kind == AgentActivityKind.TOOL_USE
        assert signal.error_message is not None
        watchdog.record_error_activity(signal.error_message)
        clock.advance(34.0)
        if _evaluate(watchdog) == WatchdogVerdict.FIRE:
            fired = True
            break
    assert fired
    assert watchdog.last_fire_reason == WatchdogFireReason.REPEATED_ERROR_LOOP


def test_opencode_string_form_error_preserves_message() -> None:
    strategy = OpenCodeExecutionStrategy()
    line = json.dumps({"type": "error", "error": _TIMEOUT_ERROR})
    signal = strategy.classify_activity_line(line)
    assert signal is not None
    assert signal.kind == AgentActivityKind.ERROR_LINE
    assert "32001" in signal.raw


def test_window_rule_fires_even_when_a_distinct_line_trails_the_storm() -> None:
    clock = FakeClock()
    watchdog = IdleWatchdog(_policy(consecutive=None, window_count=8, window_seconds=600.0), clock)
    for _ in range(8):
        watchdog.record_error_activity(_TIMEOUT_ERROR)
        clock.advance(10.0)
    watchdog.record_error_activity("MCP error -32099: unrelated blip")
    assert _evaluate(watchdog) == WatchdogVerdict.FIRE
    assert watchdog.last_fire_reason == WatchdogFireReason.REPEATED_ERROR_LOOP


def test_disabled_thresholds_never_fire_repeated_error_loop() -> None:
    clock = FakeClock()
    watchdog = IdleWatchdog(
        _policy(idle_timeout_seconds=300.0, consecutive=None, window_count=None),
        clock,
    )
    for _ in range(50):
        watchdog.record_error_activity(_TIMEOUT_ERROR)
        clock.advance(1.0)
        verdict = _evaluate(watchdog)
        assert verdict == WatchdogVerdict.CONTINUE
        assert watchdog.last_fire_reason is None


def test_repeated_error_loop_fires_when_max_session_seconds_is_configured() -> None:
    clock = FakeClock()
    watchdog = IdleWatchdog(
        TimeoutPolicy(
            idle_timeout_seconds=300.0,
            drain_window_seconds=0.0,
            max_session_seconds=3300.0,
            repeated_error_consecutive_threshold=5,
            repeated_error_window_count=8,
            repeated_error_window_seconds=600.0,
        ),
        clock,
    )
    for _ in range(4):
        watchdog.record_error_activity(_TIMEOUT_ERROR)
        clock.advance(34.0)
        assert _evaluate(watchdog) == WatchdogVerdict.CONTINUE
    watchdog.record_error_activity(_TIMEOUT_ERROR)
    assert _evaluate(watchdog) == WatchdogVerdict.FIRE
    assert watchdog.last_fire_reason == WatchdogFireReason.REPEATED_ERROR_LOOP


def test_session_ceiling_still_wins_over_repeated_error_loop() -> None:
    clock = FakeClock()
    watchdog = IdleWatchdog(
        TimeoutPolicy(
            idle_timeout_seconds=10.0,
            drain_window_seconds=0.0,
            max_session_seconds=20.0,
            repeated_error_consecutive_threshold=5,
            repeated_error_window_count=8,
            repeated_error_window_seconds=600.0,
        ),
        clock,
    )
    for _ in range(5):
        watchdog.record_error_activity(_TIMEOUT_ERROR)
    clock.advance(25.0)
    assert _evaluate(watchdog) == WatchdogVerdict.FIRE
    assert watchdog.last_fire_reason == WatchdogFireReason.SESSION_CEILING_EXCEEDED
