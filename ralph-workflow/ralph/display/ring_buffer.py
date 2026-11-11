
import collections
import threading

PARALLEL_DISPLAY_BUFFER_SIZE: int = 1000


class RingBuffer:

    def __init__(self, maxsize: int) -> None:
        self._buf: collections.deque[str] = collections.deque(
            maxlen=maxsize
        )
        self._lock = threading.Lock()
        self._dropped_count: int = 0

    def enqueue(self, item: str) -> None:
        with self._lock:
            if len(self._buf) == self._buf.maxlen:
                self._dropped_count += 1
            self._buf.append(item)

    def drain(self) -> list[str]:
        with self._lock:
            items = list(self._buf)
            self._buf.clear()
            return items

    def snapshot(self, n: int | None = None) -> list[str]:
        with self._lock:
            items = list(self._buf)
            if n is None:
                return items
            if n == 0:
                return []
            return items[-n:]

    def consume_drop_delta(self) -> int:
        with self._lock:
            delta = self._dropped_count
            self._dropped_count = 0
            return delta

    @property
    def dropped_count(self) -> int:
        with self._lock:
            return self._dropped_count
