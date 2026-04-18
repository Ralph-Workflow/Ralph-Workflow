"""Tests for RingBuffer bounded ring buffer."""

import threading

from ralph.display.ring_buffer import PARALLEL_DISPLAY_BUFFER_SIZE, RingBuffer

DEFAULT_BUFFER_SIZE = 1000
EXPECTED_DROPPED_ITEMS = 2
EXPECTED_DRAINED_ITEMS = 3


def test_buffer_size_constant_exists() -> None:
    assert PARALLEL_DISPLAY_BUFFER_SIZE == DEFAULT_BUFFER_SIZE


def test_enqueue_and_drain_basic() -> None:
    buf = RingBuffer(maxsize=5)
    buf.enqueue("a")
    buf.enqueue("b")
    buf.enqueue("c")
    items = buf.drain()
    assert items == ["a", "b", "c"]


def test_drain_empties_buffer() -> None:
    buf = RingBuffer(maxsize=5)
    buf.enqueue("x")
    buf.drain()
    assert buf.drain() == []


def test_drop_oldest_on_overflow() -> None:
    buf = RingBuffer(maxsize=3)
    buf.enqueue("1")
    buf.enqueue("2")
    buf.enqueue("3")
    buf.enqueue("4")  # drops "1"
    buf.enqueue("5")  # drops "2"
    items = buf.drain()
    assert items == ["3", "4", "5"]


def test_dropped_count_zero_initially() -> None:
    buf = RingBuffer(maxsize=5)
    assert buf.dropped_count == 0


def test_dropped_count_increments() -> None:
    buf = RingBuffer(maxsize=3)
    for i in range(5):
        buf.enqueue(str(i))
    assert buf.dropped_count == EXPECTED_DROPPED_ITEMS


def test_enqueue_five_drain_three() -> None:
    buf = RingBuffer(maxsize=3)
    for i in range(5):
        buf.enqueue(str(i))
    items = buf.drain()
    assert len(items) == EXPECTED_DRAINED_ITEMS
    assert buf.dropped_count == EXPECTED_DROPPED_ITEMS


def test_thread_safety_conservation() -> None:
    """10 threads x 1000 enqueues; dropped + drained == 10000."""
    buf = RingBuffer(maxsize=PARALLEL_DISPLAY_BUFFER_SIZE)
    num_threads = 10
    per_thread = 1000
    total = num_threads * per_thread

    threads = [
        threading.Thread(target=lambda: [buf.enqueue(f"item-{i}") for i in range(per_thread)])
        for _ in range(num_threads)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    drained = buf.drain()
    assert buf.dropped_count + len(drained) == total


def test_drain_returns_list_of_str() -> None:
    buf = RingBuffer(maxsize=3)
    buf.enqueue("hello")
    result = buf.drain()
    assert isinstance(result, list)
    assert all(isinstance(s, str) for s in result)
