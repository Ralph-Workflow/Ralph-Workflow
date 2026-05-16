"""Tests for the clock protocol and timeout clock implementations."""

from __future__ import annotations

import threading
import time

from ralph.agents.timeout_clock import Clock, FakeClock, SystemClock

_SLEEP_UPPER_BOUND_S = 0.5
_WALL_INSTANT_THRESHOLD_S = 0.05
_FAKE_START = 42.0
_BIG_SLEEP = 60.0
_SMALL_ADVANCE = 5.0
_SMALL_START = 10.0
_SYSTEM_SLEEP = 0.01


def test_system_clock_monotonic_is_monotonic() -> None:
    clock = SystemClock()
    first = clock.monotonic()
    second = clock.monotonic()
    assert second >= first


def test_system_clock_sleep_advances_time() -> None:
    clock = SystemClock()
    before = time.monotonic()
    clock.sleep(_SYSTEM_SLEEP)
    elapsed = time.monotonic() - before
    assert elapsed >= _SYSTEM_SLEEP
    assert elapsed < _SLEEP_UPPER_BOUND_S


def test_fake_clock_monotonic_does_not_advance_without_advance_call() -> None:
    clock = FakeClock(start=_FAKE_START)
    assert clock.monotonic() == _FAKE_START
    assert clock.monotonic() == _FAKE_START


def test_fake_clock_sleep_advances_logical_time_without_real_wait() -> None:
    clock = FakeClock(start=0.0)
    wall_before = time.monotonic()
    clock.sleep(_BIG_SLEEP)
    wall_elapsed = time.monotonic() - wall_before
    assert wall_elapsed < _WALL_INSTANT_THRESHOLD_S
    assert clock.monotonic() == _BIG_SLEEP


def test_fake_clock_advance_increments_logical_time_only() -> None:
    clock = FakeClock(start=_SMALL_START)
    wall_before = time.monotonic()
    clock.advance(_SMALL_ADVANCE)
    wall_elapsed = time.monotonic() - wall_before
    assert clock.monotonic() == _SMALL_START + _SMALL_ADVANCE
    assert wall_elapsed < _WALL_INSTANT_THRESHOLD_S


def test_fake_clock_runtime_checkable() -> None:
    assert isinstance(FakeClock(), Clock)


def test_system_clock_runtime_checkable() -> None:
    assert isinstance(SystemClock(), Clock) is True


_EVENT_WAIT_UPPER_BOUND_S = 0.5
_FAST_WAIT_S = 0.05
_FAKE_WAIT_LONG_S = 10.0
_FAKE_WAIT_SHORT_S = 5.0


def test_system_clock_wait_for_event_returns_immediately_when_set() -> None:
    """wait_for_event returns True immediately when event is pre-set."""
    clock = SystemClock()
    event = threading.Event()
    event.set()
    before = time.monotonic()
    result = clock.wait_for_event(event, 1.0)
    elapsed = time.monotonic() - before
    assert result is True
    assert elapsed < _WALL_INSTANT_THRESHOLD_S


def test_system_clock_wait_for_event_times_out_when_unset() -> None:
    """wait_for_event returns False after timeout when event is never set."""
    clock = SystemClock()
    event = threading.Event()
    before = time.monotonic()
    result = clock.wait_for_event(event, _FAST_WAIT_S)
    elapsed = time.monotonic() - before
    assert result is False
    assert elapsed >= _FAST_WAIT_S
    assert elapsed < _EVENT_WAIT_UPPER_BOUND_S


def test_fake_clock_wait_for_event_advances_time() -> None:
    """FakeClock.wait_for_event advances logical time without real wait."""
    clock = FakeClock(start=0.0)
    event = threading.Event()
    result = clock.wait_for_event(event, _FAKE_WAIT_LONG_S)
    assert clock.monotonic() == _FAKE_WAIT_LONG_S
    assert result is False  # event not set


def test_fake_clock_wait_for_event_returns_true_when_event_set() -> None:
    """FakeClock.wait_for_event returns True when event is already set."""
    clock = FakeClock(start=0.0)
    event = threading.Event()
    event.set()
    result = clock.wait_for_event(event, _FAKE_WAIT_SHORT_S)
    assert result is True
    assert clock.monotonic() == _FAKE_WAIT_SHORT_S
