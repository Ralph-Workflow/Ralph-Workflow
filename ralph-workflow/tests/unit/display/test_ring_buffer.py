from __future__ import annotations

from ralph.display.ring_buffer import RingBuffer

EXPECTED_DROPPED_COUNT = 10


def test_snapshot_returns_last_n_items_without_clearing_buffer() -> None:
    buffer = RingBuffer(maxsize=100)
    for item in map(str, range(5)):
        buffer.enqueue(item)

    assert buffer.snapshot(3) == ["2", "3", "4"]
    assert buffer.snapshot(3) == ["2", "3", "4"]


def test_snapshot_without_argument_returns_all_items() -> None:
    buffer = RingBuffer(maxsize=100)
    for item in map(str, range(5)):
        buffer.enqueue(item)

    assert buffer.snapshot() == ["0", "1", "2", "3", "4"]


def test_snapshot_zero_returns_empty_list() -> None:
    buffer = RingBuffer(maxsize=100)
    for item in map(str, range(5)):
        buffer.enqueue(item)

    assert buffer.snapshot(0) == []


def test_dropped_count_reflects_bounded_overflow() -> None:
    buffer = RingBuffer(maxsize=100)
    for item in map(str, range(100 + EXPECTED_DROPPED_COUNT)):
        buffer.enqueue(item)

    assert buffer.dropped_count == EXPECTED_DROPPED_COUNT


def test_drain_then_snapshot_returns_empty_without_incrementing_dropped_count() -> None:
    buffer = RingBuffer(maxsize=100)
    for item in map(str, range(5)):
        buffer.enqueue(item)

    assert buffer.drain() == ["0", "1", "2", "3", "4"]
    assert buffer.snapshot() == []
    assert buffer.dropped_count == 0
