"""Black-box tests for IdleWatchdog policy using FakeClock."""

from __future__ import annotations

import pytest
from loguru import logger

from ralph.agents.execution_state import AgentExecutionState
from ralph.agents.idle_watchdog import (
    IdleWatchdog,
    TimeoutPolicy,
    WatchdogFireReason,
    WatchdogVerdict,
)
from ralph.agents.timeout_clock import FakeClock


def _make_watchdog(
    idle_timeout: float | None,
    drain_window: float = 0.5,
    max_waiting: float | None = None,
    start: float = 0.0,
    max_session: float | None = None,
) -> tuple[IdleWatchdog, FakeClock]:
    if max_waiting is None:
        max_waiting = max(1800.0, idle_timeout) if idle_timeout is not None else 1800.0
    config = TimeoutPolicy(
        idle_timeout_seconds=idle_timeout,
        drain_window_seconds=drain_window,
        max_waiting_on_child_seconds=max_waiting,
        max_session_seconds=max_session,
    )
    clock = FakeClock(start=start)
    return IdleWatchdog(config, clock), clock


def _active() -> AgentExecutionState:
    return AgentExecutionState.ACTIVE


def _waiting() -> AgentExecutionState:
    return AgentExecutionState.WAITING_ON_CHILD


def test_disabled_when_idle_timeout_is_none() -> None:
    watchdog, clock = _make_watchdog(idle_timeout=None)
    clock.advance(1_000_000)
    assert watchdog.evaluate(classify_quiet=_active) == WatchdogVerdict.CONTINUE
    assert watchdog.evaluate(classify_quiet=_waiting) == WatchdogVerdict.CONTINUE


def test_continues_before_deadline() -> None:
    watchdog, clock = _make_watchdog(idle_timeout=10)
    clock.advance(9.9)
    result = watchdog.evaluate(classify_quiet=_active)
    assert result == WatchdogVerdict.CONTINUE


def test_enters_drain_window_at_deadline_when_active() -> None:
    watchdog, clock = _make_watchdog(idle_timeout=10, drain_window=0.5)

    # At deadline, classify_quiet=ACTIVE -> enter drain window
    clock.advance(10.0)
    result = watchdog.evaluate(classify_quiet=_active)
    assert result == WatchdogVerdict.CONTINUE  # drain window entered

    # Still inside drain window
    clock.advance(0.4)
    result = watchdog.evaluate(classify_quiet=_active)
    assert result == WatchdogVerdict.CONTINUE

    # Drain window exhausted
    clock.advance(0.2)
    result = watchdog.evaluate(classify_quiet=_active)
    assert result == WatchdogVerdict.FIRE
    assert watchdog.last_fire_reason == WatchdogFireReason.NO_OUTPUT_DEADLINE


def test_drain_window_aborted_by_late_activity() -> None:
    watchdog, clock = _make_watchdog(idle_timeout=10, drain_window=0.5)

    # Enter drain window
    clock.advance(10.0)
    result = watchdog.evaluate(classify_quiet=_active)
    assert result == WatchdogVerdict.CONTINUE

    # Activity arrives during drain
    clock.advance(0.2)
    watchdog.record_activity()

    # Should continue without firing; advance well under idle timeout
    clock.advance(5.0)
    result = watchdog.evaluate(classify_quiet=_active)
    assert result == WatchdogVerdict.CONTINUE


def test_waiting_on_child_defers_without_resetting_activity() -> None:
    watchdog, clock = _make_watchdog(idle_timeout=10, max_waiting=1800.0)

    # Past idle deadline with children present
    clock.advance(11.0)
    result = watchdog.evaluate(classify_quiet=_waiting)
    assert result == WatchdogVerdict.WAITING_ON_CHILD

    # More time passes; children gone but no new output
    clock.advance(5.0)
    result = watchdog.evaluate(classify_quiet=_active)
    # Still past idle deadline (16s > 10s), no new activity -> drain window opens
    assert result == WatchdogVerdict.CONTINUE

    # Drain exhausted
    clock.advance(1.0)
    result = watchdog.evaluate(classify_quiet=_active)
    assert result == WatchdogVerdict.FIRE


def test_waiting_on_child_hard_ceiling_fires() -> None:
    watchdog, clock = _make_watchdog(idle_timeout=10, max_waiting=20.0)

    # Advance past idle deadline
    clock.advance(11.0)
    result = watchdog.evaluate(classify_quiet=_waiting)
    assert result == WatchdogVerdict.WAITING_ON_CHILD

    # Advance in a single jump past the hard ceiling
    clock.advance(20.0)
    result = watchdog.evaluate(classify_quiet=_waiting)
    assert result == WatchdogVerdict.FIRE
    assert watchdog.last_fire_reason == WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG


def test_record_activity_clears_drain_state() -> None:
    watchdog, clock = _make_watchdog(idle_timeout=10, drain_window=0.5)

    # Enter drain window
    clock.advance(10.0)
    watchdog.evaluate(classify_quiet=_active)

    # Reset by activity
    watchdog.record_activity()

    # Advance less than idle timeout from the activity point
    clock.advance(5.0)
    result = watchdog.evaluate(classify_quiet=_active)
    assert result == WatchdogVerdict.CONTINUE


def test_validation_rejects_zero_idle_timeout() -> None:
    with pytest.raises(ValueError, match="positive"):
        TimeoutPolicy(idle_timeout_seconds=0)


def test_validation_rejects_negative_drain_window() -> None:
    with pytest.raises(ValueError, match=">="):
        TimeoutPolicy(idle_timeout_seconds=10, drain_window_seconds=-0.1)


def test_validation_rejects_max_waiting_less_than_idle() -> None:
    with pytest.raises(ValueError, match="max_waiting_on_child_seconds"):
        TimeoutPolicy(idle_timeout_seconds=100, max_waiting_on_child_seconds=50)


def test_session_ceiling_validation_rejects_value_lower_than_idle_timeout() -> None:
    """TimeoutPolicy rejects max_session_seconds < idle_timeout_seconds."""
    with pytest.raises(ValueError, match="max_session_seconds"):
        TimeoutPolicy(idle_timeout_seconds=100, max_session_seconds=50)


def test_session_ceiling_fires_despite_heartbeats() -> None:
    """Session ceiling fires even when record_activity() is called continuously.

    This tests that the session ceiling cannot be defeated by heartbeat activity —
    a process that produces output continuously must still be killed when the
    absolute session wall-clock ceiling is reached.
    """
    max_session = 30.0
    watchdog, clock = _make_watchdog(
        idle_timeout=10.0, drain_window=0.5, max_waiting=1800.0, max_session=max_session
    )

    # Simulate continuous heartbeat activity every second for 29s — no fire yet.
    for _ in range(29):
        clock.advance(1.0)
        watchdog.record_activity()
        result = watchdog.evaluate(classify_quiet=_active)
        assert result == WatchdogVerdict.CONTINUE, f"Expected CONTINUE at t={clock.monotonic()}"

    # At t=30s the session ceiling is reached — FIRE regardless of recent activity.
    clock.advance(1.0)
    watchdog.record_activity()  # heartbeat fires just before evaluation
    result = watchdog.evaluate(classify_quiet=_active)
    assert result == WatchdogVerdict.FIRE
    assert watchdog.last_fire_reason == WatchdogFireReason.SESSION_CEILING_EXCEEDED


def test_waiting_on_child_cumulative_survives_active_oscillation() -> None:
    """Cumulative WAITING time is preserved across WAITING->ACTIVE->WAITING oscillation.

    This tests the false-negative fix: a process that alternates between producing
    output (WAITING->ACTIVE) and waiting on children cannot defeat the
    max_waiting_on_child_seconds ceiling by resetting the counter on each active interval.
    """
    watchdog, clock = _make_watchdog(idle_timeout=10, drain_window=0.5, max_waiting=20.0)

    # (a) Advance 11s -> past idle deadline. classify=WAITING_ON_CHILD -> start run1.
    clock.advance(11.0)
    result = watchdog.evaluate(classify_quiet=_waiting)
    assert result == WatchdogVerdict.WAITING_ON_CHILD

    # (b) Advance 5s -> still past deadline. classify=ACTIVE -> accumulate run1 (5s), enter drain.
    clock.advance(5.0)
    result = watchdog.evaluate(classify_quiet=_active)
    assert result == WatchdogVerdict.CONTINUE  # drain entered
    # At this point cumulative should have 5s from run1.
    assert watchdog.cumulative_waiting_on_child_seconds == pytest.approx(5.0, abs=0.01)

    # (c) Advance 0.1s -> still in drain window. classify=WAITING -> abandon drain, start run2.
    clock.advance(0.1)
    result = watchdog.evaluate(classify_quiet=_waiting)
    assert result == WatchdogVerdict.WAITING_ON_CHILD  # drain abandoned

    # (d) Advance 6s -> run2 elapsed 6s, cumulative_candidate = 5 + 6 = 11s < 20 ceiling.
    clock.advance(6.0)
    result = watchdog.evaluate(classify_quiet=_waiting)
    assert result == WatchdogVerdict.WAITING_ON_CHILD

    # (e) Advance 10s -> run2 elapsed 16s, cumulative_candidate = 5 + 16 = 21s >= 20 ceiling.
    clock.advance(10.0)
    result = watchdog.evaluate(classify_quiet=_waiting)
    assert result == WatchdogVerdict.FIRE
    assert watchdog.last_fire_reason == WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG


def test_consecutive_waiting_does_not_double_count() -> None:
    """Consecutive WAITING evaluations must not double-count the same elapsed time."""
    watchdog, clock = _make_watchdog(idle_timeout=10, max_waiting=100.0)

    # Past idle deadline
    clock.advance(11.0)
    watchdog.evaluate(classify_quiet=_waiting)  # starts run at t=11

    clock.advance(5.0)  # t=16
    watchdog.evaluate(classify_quiet=_waiting)  # 5s elapsed in run

    clock.advance(5.0)  # t=21
    watchdog.evaluate(classify_quiet=_waiting)  # 10s elapsed in run

    # cumulative is still 0 (only added on transition out of WAITING)
    # candidate_total should be 10s, well under ceiling
    assert watchdog.cumulative_waiting_on_child_seconds == 0.0
    assert watchdog.last_fire_reason is None


def test_drain_window_defers_when_children_reappear() -> None:
    """When children appear during the drain window, drain is abandoned and WAITING resumes.

    This tests the false-positive fix: children that appear during the drain window
    must prevent the timeout from firing.
    """
    watchdog, clock = _make_watchdog(
        idle_timeout=10, drain_window=0.5, max_waiting=1800.0
    )

    # (a) Advance 10s -> at idle deadline. classify=ACTIVE -> enter drain window.
    clock.advance(10.0)
    result = watchdog.evaluate(classify_quiet=_active)
    assert result == WatchdogVerdict.CONTINUE  # drain entered
    assert watchdog._in_drain_window is True

    # (b) Advance 0.2s -> inside drain window. classify=WAITING -> abandon drain.
    clock.advance(0.2)
    result = watchdog.evaluate(classify_quiet=_waiting)
    assert result == WatchdogVerdict.WAITING_ON_CHILD  # not FIRE
    assert watchdog._in_drain_window is False  # drain abandoned

    # (c) Advance 5s -> back to ACTIVE -> re-enter drain.
    clock.advance(5.0)
    result = watchdog.evaluate(classify_quiet=_active)
    assert result == WatchdogVerdict.CONTINUE  # new drain entered

    # (d) Advance 0.6s -> drain exhausted (0.5s + 0.1s overshoot). Fires NO_OUTPUT_DEADLINE.
    clock.advance(0.6)
    result = watchdog.evaluate(classify_quiet=_active)
    assert result == WatchdogVerdict.FIRE
    assert watchdog.last_fire_reason == WatchdogFireReason.NO_OUTPUT_DEADLINE


def test_logger_emits_warning_on_fire_with_reason() -> None:
    """FIRE verdict emits a loguru WARNING with the fire reason."""
    captured_messages: list[str] = []

    def _sink(message: object) -> None:
        captured_messages.append(str(message))

    sink_id = logger.add(
        _sink,
        level="WARNING",
        filter=lambda r: r["extra"].get("component") == "idle_watchdog",
    )
    try:
        watchdog, clock = _make_watchdog(idle_timeout=10, drain_window=0.0, max_waiting=1800.0)

        # Advance past idle deadline and fire immediately (drain_window=0)
        clock.advance(10.0)
        result = watchdog.evaluate(classify_quiet=_active)
        assert result == WatchdogVerdict.FIRE  # drain_window=0 fires immediately
        assert watchdog.last_fire_reason == WatchdogFireReason.NO_OUTPUT_DEADLINE
    finally:
        logger.remove(sink_id)

    assert any("FIRE" in msg or "no_output_deadline" in msg for msg in captured_messages), (
        f"Expected WARNING with fire reason, got: {captured_messages}"
    )
