from __future__ import annotations

from collections import deque
from contextlib import suppress
from itertools import chain, islice
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator, Sequence


def _notify(sink: Callable[[str], None], line: str) -> None:
    with suppress(Exception):
        sink(line)


class BoundedLinesQueue:
    __slots__ = ("_deque", "_eviction_sink")

    def __init__(self, maxlen: int) -> None:
        if maxlen <= 0:
            raise ValueError("maxlen must be positive")
        self._deque: deque[str] = deque(maxlen=maxlen)
        self._eviction_sink: Callable[[str], None] | None = None

    @property
    def maxlen(self) -> int:
        assert self._deque.maxlen is not None
        return self._deque.maxlen

    def __len__(self) -> int:
        return len(self._deque)

    def __bool__(self) -> bool:
        return bool(self._deque)

    def set_eviction_sink(self, sink: Callable[[str], None] | None) -> None:
        self._eviction_sink = sink

    def _evict_for(self, incoming: Sequence[str]) -> None:
        sink = self._eviction_sink
        if sink is None or not incoming:
            return
        overflow = len(self._deque) + len(incoming) - self.maxlen
        if overflow <= 0:
            return
        for line in islice(chain(self._deque, incoming), overflow):
            _notify(sink, line)

    def append(self, line: str) -> None:
        self._evict_for((line,))
        self._deque.append(line)

    def extend(self, lines: Iterable[str]) -> None:
        materialised = list(lines)
        self._evict_for(materialised)
        self._deque.extend(materialised)

    def popleft(self) -> str:
        return self._deque.popleft()

    def snapshot(self) -> list[str]:
        return list(self._deque)

    def clear(self) -> None:
        self._deque.clear()

    def __iter__(self) -> Iterator[str]:
        return iter(self._deque)


__all__ = ["BoundedLinesQueue"]
