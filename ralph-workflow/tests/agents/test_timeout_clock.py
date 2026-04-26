"""Tests for the Clock protocol and implementations in timeout_clock.py."""

from __future__ import annotations

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
