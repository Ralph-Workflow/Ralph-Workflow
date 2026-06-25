"""Bounded pre-parse line queue shared by subprocess and PTY readers.

wt-024 Step 10: the pre-parse ``_lines_queue`` on both
``_ProcessLineReader`` and ``PtyLineReader`` was an unbounded
``list[str]`` that could spike memory under burst output (e.g.
``find /`` on a large tree). The bounded wrapper here exposes the
same surface as the prior list (append / extend / popleft /
snapshot / clear) but with a ``maxlen`` cap that drops the oldest
entry when the producer outpaces the consumer. The cap is aligned
to the post-parse tail (``_MAX_PARSED_OUTPUT_LINES``) so the
contract is consistent across both buffers.

The class is intentionally minimal: no locking, no logging, no
metrics. Locking is provided by the surrounding reader's
``_lines_lock`` (a ``threading.Lock``); logging and metrics live
in the readers' production paths.
"""

from __future__ import annotations

from collections import deque
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator


class BoundedLinesQueue:
    """A drop-oldest queue of lines with the ``list[str]``-like surface.

    Backed by ``collections.deque(maxlen=cap)`` so ``append`` and
    ``extend`` are O(1) amortized and drop the oldest item when the
    queue is at capacity. ``popleft`` is O(1); ``snapshot`` is O(n)
    (returns a fresh list).
    """

    __slots__ = ("_deque",)

    def __init__(self, maxlen: int) -> None:
        if maxlen <= 0:
            raise ValueError("maxlen must be positive")
        self._deque: deque[str] = deque(maxlen=maxlen)

    @property
    def maxlen(self) -> int:
        """Return the maximum number of items this queue can hold."""
        assert self._deque.maxlen is not None
        return self._deque.maxlen

    def __len__(self) -> int:
        return len(self._deque)

    def __bool__(self) -> bool:
        return bool(self._deque)

    def append(self, line: str) -> None:
        """Append a single line, dropping the oldest when at capacity."""
        self._deque.append(line)

    def extend(self, lines: Iterable[str]) -> None:
        """Append multiple lines, dropping the oldest as needed."""
        self._deque.extend(lines)

    def popleft(self) -> str:
        """Remove and return the leftmost line. Raises IndexError when empty."""
        return self._deque.popleft()

    def snapshot(self) -> list[str]:
        """Return a list copy of the current contents in insertion order."""
        return list(self._deque)

    def clear(self) -> None:
        """Drop every item from the queue."""
        self._deque.clear()

    def __iter__(self) -> Iterator[str]:
        return iter(self._deque)


__all__ = ["BoundedLinesQueue"]
