"""Watchdog tests for report_progress repetition handling.

Part of closing the 5-hour-runaway hole: the agent stayed "alive" partly because
every ``report_progress`` call reset the stall timers, so a cosmetic heartbeat
repeating the same status forever looked like forward progress. Fixed behavior:
a progress report that REPEATS the previous status counts toward the
repeated-error circuit breaker and does not reset the idle baseline, while a
progress report whose status CHANGES counts as genuine progress.
"""

from __future__ import annotations

from ralph.agents.execution_state import AgentExecutionState
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
    return watchdog.evaluate(
        classify_quiet=lambda: AgentExecutionState.RESUMABLE_CONTINUE
    )


_STUCK = "status='Verifying state from read-only tools; exec is timing out' note=''"


def test_repeated_identical_progress_reports_trip_the_circuit_breaker() -> None:
    clock = FakeClock()
    watchdog = IdleWatchdog(_policy(consecutive=5), clock)
    watchdog.record_progress_report(_STUCK)  # first occurrence establishes the status
    for _ in range(4):
        watchdog.record_progress_report(_STUCK)
        clock.advance(34.0)
        assert _evaluate(watchdog) == WatchdogVerdict.CONTINUE
    watchdog.record_progress_report(_STUCK)  # 5th repeat
    assert _evaluate(watchdog) == WatchdogVerdict.FIRE
    assert watchdog.last_fire_reason == WatchdogFireReason.REPEATED_ERROR_LOOP


def test_changing_progress_reports_never_trip() -> None:
    clock = FakeClock()
    watchdog = IdleWatchdog(_policy(consecutive=5), clock)
    for index in range(30):
        watchdog.record_progress_report(f"status='completed step {index}' note=''")
        clock.advance(10.0)
        assert _evaluate(watchdog) == WatchdogVerdict.CONTINUE


def test_changed_progress_report_resets_idle_baseline() -> None:
    clock = FakeClock()
    watchdog = IdleWatchdog(
        _policy(idle_timeout_seconds=300.0, consecutive=None, window_count=None),
        clock,
    )
    watchdog.record_progress_report("status='starting' note=''")
    clock.advance(200.0)
    watchdog.record_progress_report("status='now doing real work' note=''")
    clock.advance(200.0)  # only 200s since the changed (progress) report
    assert _evaluate(watchdog) == WatchdogVerdict.CONTINUE
