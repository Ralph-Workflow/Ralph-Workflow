"""Black-box tests for IdleWatchdog policy using FakeClock."""

from __future__ import annotations

import pytest

from ralph.agents.execution_state import AgentExecutionState
from ralph.agents.idle_watchdog import (
    IdleWatchdog,
    WatchdogConfig,
    WatchdogFireReason,
    WatchdogVerdict,
)
from ralph.agents.timeout_clock import FakeClock


def _make_watchdog(
    idle_timeout: float | None,
    drain_window: float = 0.5,
    max_waiting: float | None = None,
    start: float = 0.0,
) -> tuple[IdleWatchdog, FakeClock]:
    if max_waiting is None:
        max_waiting = max(1800.0, idle_timeout) if idle_timeout is not None else 1800.0
    config = WatchdogConfig(
        idle_timeout_seconds=idle_timeout,
        drain_window_seconds=drain_window,
        max_waiting_on_child_seconds=max_waiting,
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
        WatchdogConfig(idle_timeout_seconds=0)


def test_validation_rejects_negative_drain_window() -> None:
    with pytest.raises(ValueError, match=">="):
        WatchdogConfig(idle_timeout_seconds=10, drain_window_seconds=-0.1)


def test_validation_rejects_max_waiting_less_than_idle() -> None:
    with pytest.raises(ValueError, match="max_waiting_on_child_seconds"):
        WatchdogConfig(idle_timeout_seconds=100, max_waiting_on_child_seconds=50)
