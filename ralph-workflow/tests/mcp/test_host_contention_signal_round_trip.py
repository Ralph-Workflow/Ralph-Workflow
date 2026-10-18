from collections.abc import Generator

import pytest

from ralph.mcp.server._mcp_health_event import (
    _clear_host_contention_signals,
    pop_recent_host_contention_signals,
    record_host_contention_signal,
)


class _FakeClock:
    """Controllable monotonic clock for time-injection tests."""

    def __init__(self, start: float = 0.0) -> None:
        self.now: float = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture(autouse=True)
def _isolate_contention_signals() -> Generator[None, None, None]:
    """Clear the module-level contention deque before and after each test."""
    _clear_host_contention_signals()
    yield
    _clear_host_contention_signals()


def test_record_and_pop_round_trip() -> None:
    _clear_host_contention_signals()
    record_host_contention_signal("watch_capacity_predicted")
    sigs = pop_recent_host_contention_signals(60.0)
    assert len(sigs) == 1
    assert sigs[0][1] == "watch_capacity_predicted"


def test_window_filters_old_signals() -> None:
    _clear_host_contention_signals()
    clock = _FakeClock(start=100.0)
    # Record the first signal at t=100.0 via the injected clock.
    record_host_contention_signal("old", _now=clock)
    # Advance the clock to 109.0 so the first signal is 9.0s old.
    clock.advance(9.0)
    # A 5.0s window must exclude the 9.0s-old signal.
    sigs_old = pop_recent_host_contention_signals(5.0, _now=clock)
    assert sigs_old == ()
    # Record a fresh signal at t=109.0.
    record_host_contention_signal("fresh", _now=clock)
    # The same 5.0s window now includes the new signal and excludes the old.
    sigs_mixed = pop_recent_host_contention_signals(5.0, _now=clock)
    reasons = [reason for _ts, reason in sigs_mixed]
    assert "fresh" in reasons
    assert "old" not in reasons
    assert len(sigs_mixed) == 1


def test_deque_keeps_latest_64_after_overflow() -> None:
    _clear_host_contention_signals()
    # A stable clock makes every recorded (ts, reason) share ts=0.0
    # so the window only filters on deque order, not on timestamps.
    clock = _FakeClock(start=0.0)
    for i in range(200):
        record_host_contention_signal(f"overflow-{i}", _now=clock)
    sigs = pop_recent_host_contention_signals(60.0, _now=clock)
    # Deque cap is 64, FIFO drop-oldest.
    assert len(sigs) == 64
    reasons = [reason for _ts, reason in sigs]
    expected = [f"overflow-{i}" for i in range(136, 200)]
    assert reasons == expected


def test_deque_is_bounded() -> None:
    _clear_host_contention_signals()
    for i in range(200):
        record_host_contention_signal(f"reason-{i}")
    # Cap is 64, so the deque length is at most 64.
    assert len(pop_recent_host_contention_signals(60.0)) <= 64
