"""Bounded ring buffer with drop-oldest policy and dropped-item counter.

Used by ParallelDisplay to absorb burst output from agents without OOM risk.
Thread-safe via threading.Lock — NOT asyncio-safe.
"""

import collections
import threading

PARALLEL_DISPLAY_BUFFER_SIZE: int = 1000


class RingBuffer:
    """Thread-safe bounded ring buffer with drop-oldest overflow policy.

    When the buffer is full and a new item arrives, the oldest item is dropped
    and `dropped_count` is incremented.

    Args:
        maxsize: Maximum number of items to retain.
    """

    def __init__(self, maxsize: int) -> None:
        self._buf: collections.deque[str] = collections.deque(maxlen=maxsize)
        self._lock = threading.Lock()
        self._dropped_count: int = 0

    def enqueue(self, item: str) -> None:
        with self._lock:
            if len(self._buf) == self._buf.maxlen:
                self._dropped_count += 1
            self._buf.append(item)

    def drain(self) -> list[str]:
        """Destructively return and clear buffered items."""
        with self._lock:
            items = list(self._buf)
            self._buf.clear()
            return items

    def snapshot(self, n: int | None = None) -> list[str]:
        """Return buffered items without clearing them.

        `drain()` is destructive; `snapshot()` is the non-destructive, read-only
        view for callers that need to inspect buffered output.
        """
        with self._lock:
            items = list(self._buf)
            if n is None:
                return items
            if n == 0:
                return []
            return items[-n:]

    @property
    def dropped_count(self) -> int:
        with self._lock:
            return self._dropped_count
