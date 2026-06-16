"""Watchdog-level tests for the repeated-error circuit breaker.

The incident: an agent re-emitted the identical ``MCP error -32001: Request
timed out`` every ~34s for ~5 hours and nothing aborted it, because each error
line was treated as ordinary output that reset the idle timer. These tests pin
the fixed behavior: the watchdog fires ``REPEATED_ERROR_LOOP`` when an agent
repeats the same error without forward progress, error lines no longer mask the
idle deadline, and genuine output resets the streak.

All timing uses an injected ``FakeClock`` — no real waits.
"""

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


def test_real_output_between_errors_prevents_repeated_error_fire() -> None:
    clock = FakeClock()
    watchdog = IdleWatchdog(_policy(consecutive=5), clock)
    for _ in range(10):
        watchdog.record_error_activity(_TIMEOUT_ERROR)
        clock.advance(10.0)
        watchdog.record_activity()  # genuine forward progress resets the streak
        clock.advance(10.0)
        assert _evaluate(watchdog) == WatchdogVerdict.CONTINUE


def test_error_lines_do_not_reset_the_idle_deadline() -> None:
    clock = FakeClock()
    # Threshold high enough that the repeated-error rule cannot fire first.
    watchdog = IdleWatchdog(
        _policy(idle_timeout_seconds=300.0, consecutive=None, window_count=None),
        clock,
    )
    watchdog.record_error_activity(_TIMEOUT_ERROR)
    clock.advance(301.0)  # error did not reset idle -> idle deadline elapses
    assert _evaluate(watchdog) == WatchdogVerdict.FIRE
    assert watchdog.last_fire_reason == WatchdogFireReason.NO_OUTPUT_DEADLINE


def test_opencode_toplevel_error_line_classified_as_error_line() -> None:
    """End-to-end: a raw top-level error NDJSON line classifies as ERROR_LINE."""
    strategy = OpenCodeExecutionStrategy()
    line = json.dumps({"type": "error", "error": {"message": _TIMEOUT_ERROR}})
    signal = strategy.classify_activity_line(line)
    assert signal is not None
    assert signal.kind == AgentActivityKind.ERROR_LINE
    assert "32001" in signal.raw


def test_opencode_tool_state_error_line_classified_as_error_line() -> None:
    """End-to-end: an MCP tool-call failure (tool_use with state.status=error)
    classifies as ERROR_LINE, not OUTPUT_LINE — otherwise the retry storm would
    reset the idle/streak timers and the breaker would never fire."""
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
    assert signal.kind == AgentActivityKind.ERROR_LINE
    assert "32001" in signal.raw


def test_tool_state_error_storm_fires_repeated_error_loop_end_to_end() -> None:
    """Feed real raw tool-state-error NDJSON lines through the strategy and into
    the watchdog: the incident pattern must abort via REPEATED_ERROR_LOOP."""
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
        assert signal.kind == AgentActivityKind.ERROR_LINE
        watchdog.record_error_activity(signal.raw)
        clock.advance(34.0)
        if _evaluate(watchdog) == WatchdogVerdict.FIRE:
            fired = True
            break
    assert fired
    assert watchdog.last_fire_reason == WatchdogFireReason.REPEATED_ERROR_LOOP


def test_opencode_string_form_error_preserves_message() -> None:
    """A top-level error whose ``error`` is a bare string keeps the message
    (and its -32001 code) rather than collapsing to 'unknown error'."""
    strategy = OpenCodeExecutionStrategy()
    line = json.dumps({"type": "error", "error": _TIMEOUT_ERROR})
    signal = strategy.classify_activity_line(line)
    assert signal is not None
    assert signal.kind == AgentActivityKind.ERROR_LINE
    assert "32001" in signal.raw


def test_window_rule_fires_even_when_a_distinct_line_trails_the_storm() -> None:
    """Regression: a single distinct trailing line must NOT mask a window-full
    storm of an earlier fingerprint."""
    clock = FakeClock()
    watchdog = IdleWatchdog(_policy(consecutive=None, window_count=8, window_seconds=600.0), clock)
    for _ in range(8):
        watchdog.record_error_activity(_TIMEOUT_ERROR)
        clock.advance(10.0)
    # A single, different error arrives last — the window still holds 8 timeouts.
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
    """Regression: a prior implementation used an ``elif`` that skipped
    REPEATED_ERROR_LOOP whenever ``max_session_seconds`` was non-``None``.
    In production ``GeneralConfig.agent_max_session_seconds`` defaults to
    a non-None value (3300.0s), which silently disabled the repeated-error
    circuit breaker. The breaker must fire regardless of whether the
    session ceiling is configured: REPEATED_ERROR_LOOP is a wedged
    retry-loop signal that is independent of the operator-set hard cap
    on session wall-clock, and the two checks must coexist.
    """
    clock = FakeClock()
    # Mirror the production default: a non-None max_session_seconds at the
    # 3300.0s ceiling. The session ceiling is far in the future so it
    # cannot fire before the repeated-error rule does.
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
    """Regression: when BOTH the session ceiling AND the repeated-error
    rule would fire on the same evaluate() call, the session ceiling MUST
    take priority (it is the absolute, gate-bypassing reason). The
    repeated-error rule is gated by the smart-verdict gate; the session
    ceiling is not. Decoupling the two checks must not flip this priority.
    """
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
    clock.advance(25.0)  # exceeds max_session_seconds=20.0
    assert _evaluate(watchdog) == WatchdogVerdict.FIRE
    assert watchdog.last_fire_reason == WatchdogFireReason.SESSION_CEILING_EXCEEDED
