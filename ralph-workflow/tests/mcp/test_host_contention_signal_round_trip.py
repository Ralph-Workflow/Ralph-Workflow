from collections.abc import Generator

import pytest

from ralph.mcp.server._mcp_health_event import (
    _clear_host_contention_signals,
    pop_recent_host_contention_signals,
    record_host_contention_signal,
)


class _FakeClock:
    def __init__(self, start: float = 0.0) -> None:
        self.now: float = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture(autouse=True)
def _isolate_contention_signals() -> Generator[None, None, None]:
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
    record_host_contention_signal("old", _now=clock)
    clock.advance(9.0)
    sigs_old = pop_recent_host_contention_signals(5.0, _now=clock)
    assert sigs_old == ()
    record_host_contention_signal("fresh", _now=clock)
    sigs_mixed = pop_recent_host_contention_signals(5.0, _now=clock)
    reasons = [reason for _ts, reason in sigs_mixed]
    assert "fresh" in reasons
    assert "old" not in reasons
    assert len(sigs_mixed) == 1


def test_deque_keeps_latest_64_after_overflow() -> None:
    _clear_host_contention_signals()
    clock = _FakeClock(start=0.0)
    for i in range(200):
        record_host_contention_signal(f"overflow-{i}", _now=clock)
    sigs = pop_recent_host_contention_signals(60.0, _now=clock)
    assert len(sigs) == 64
    reasons = [reason for _ts, reason in sigs]
    expected = [f"overflow-{i}" for i in range(136, 200)]
    assert reasons == expected


def test_deque_is_bounded() -> None:
    _clear_host_contention_signals()
    for i in range(200):
        record_host_contention_signal(f"reason-{i}")
    assert len(pop_recent_host_contention_signals(60.0)) <= 64
