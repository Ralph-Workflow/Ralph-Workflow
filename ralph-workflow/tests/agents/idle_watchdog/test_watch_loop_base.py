"""Black-box tests for WatchLoopBase using FakeClock."""

from __future__ import annotations

import threading as _threading

import pytest

from ralph.agents.idle_watchdog._watch_loop_base import WatchLoopBase
from ralph.agents.system_clock import SystemClock
from ralph.agents.timeout_clock import FakeClock


def test_wait_until_returns_immediately_when_predicate_true() -> None:
    """Predicate truthy on first check -> returns value immediately, zero clock budget."""
    clock = FakeClock(start=0.0)

    def _predicate() -> int | None:
        return 42

    class _Watchdog(WatchLoopBase):
        def check(self) -> int | None:
            return self.wait_until(
                predicate=_predicate,
                timeout_s=30.0,
                poll_interval_s=0.5,
            )

    wd = _Watchdog(clock)
    result = wd.check()

    assert result == 42
    assert clock.monotonic() == 0.0


def test_wait_until_returns_value_when_predicate_becomes_true_after_ticks() -> None:
    """Predicate becomes truthy after clock advances some ticks -> returns value before deadline."""
    clock = FakeClock(start=0.0)
    call_count: list[int] = [0]

    def _predicate() -> str | None:
        call_count[0] += 1
        if call_count[0] >= 3:
            return "done"
        return None

    class _Watchdog(WatchLoopBase):
        def check(self) -> str | None:
            return self.wait_until(
                predicate=_predicate,
                timeout_s=10.0,
                poll_interval_s=0.5,
            )

    wd = _Watchdog(clock)
    result = wd.check()

    assert result == "done"
    assert call_count[0] == 3
    assert clock.monotonic() == pytest.approx(1.0, abs=0.001)


def test_wait_until_returns_none_on_timeout() -> None:
    """Predicate stays None for full timeout -> None returned; clock advances by timeout_s."""
    clock = FakeClock(start=0.0)

    def _predicate() -> None:
        return None

    class _Watchdog(WatchLoopBase):
        def check(self) -> None:
            self.wait_until(
                predicate=_predicate,
                timeout_s=3.0,
                poll_interval_s=0.5,
            )

    wd = _Watchdog(clock)
    wd.check()
    assert clock.monotonic() == pytest.approx(3.0, abs=0.001)


def test_wait_until_calls_on_tick_each_cycle() -> None:
    """on_tick is called on every poll cycle (but NOT on the first entry check)."""
    clock = FakeClock(start=0.0)
    tick_values: list[str | None] = []

    def _predicate() -> str | None:
        if len(tick_values) >= 2:
            return "found"
        return None

    class _Watchdog(WatchLoopBase):
        def check(self) -> str | None:
            return self.wait_until(
                predicate=_predicate,
                timeout_s=10.0,
                poll_interval_s=0.5,
                on_tick=tick_values.append,
            )

    wd = _Watchdog(clock)
    result = wd.check()

    assert result == "found"
    assert tick_values == [None, None]


def test_wait_until_does_not_wait_when_predicate_true_on_entry() -> None:
    """Predicate is True on first call -> no clock advance, no on_tick."""
    clock = FakeClock(start=0.0)
    tick_calls: list[int | None] = []

    def _predicate() -> int | None:
        return 99

    class _Watchdog(WatchLoopBase):
        def check(self) -> int | None:
            return self.wait_until(
                predicate=_predicate,
                timeout_s=5.0,
                poll_interval_s=0.5,
                on_tick=tick_calls.append,
            )

    wd = _Watchdog(clock)
    result = wd.check()

    assert result == 99
    assert clock.monotonic() == 0.0
    assert tick_calls == []


def test_signal_activity_wakes_wait_until_in_threaded_context() -> None:
    """signal_activity pulses the event; wait_until wakes before poll_interval_s."""
    clock = SystemClock()
    event = _threading.Event()
    predicate_value: list[bool] = [False]

    def _predicate() -> str | None:
        if predicate_value[0]:
            return "woken"
        return None

    class _Watchdog(WatchLoopBase):
        def __init__(self) -> None:
            super().__init__(clock)

        def wait(self) -> str | None:
            return self.wait_until(
                predicate=_predicate,
                timeout_s=60.0,
                poll_interval_s=10.0,
            )

    wd = _Watchdog()

    def _signal_later() -> None:
        event.wait(0.05)
        predicate_value[0] = True
        wd.signal_activity()

    t = _threading.Thread(target=_signal_later, daemon=True)
    t.start()
    result = wd.wait_until(
        predicate=_predicate,
        timeout_s=60.0,
        poll_interval_s=10.0,
    )
    t.join()

    assert result == "woken"


def test_wait_until_respects_non_divisible_timeout_boundary() -> None:
    """wait_until must not overshoot the requested timeout by a full poll interval.

    Analysis-feedback regression: with timeout_s=3.1 and poll_interval_s=0.5,
    the previous implementation always waited the full 0.5s tick, ending at
    3.5s instead of 3.1s. The fix clamps the final wait to the remaining
    deadline, so FakeClock stops at the timeout boundary.
    """
    clock = FakeClock(start=0.0)

    def _predicate() -> None:
        return None

    class _Watchdog(WatchLoopBase):
        def check(self) -> None:
            self.wait_until(
                predicate=_predicate,
                timeout_s=3.1,
                poll_interval_s=0.5,
            )

    wd = _Watchdog(clock)
    wd.check()
    assert clock.monotonic() == pytest.approx(3.1, abs=0.001)
