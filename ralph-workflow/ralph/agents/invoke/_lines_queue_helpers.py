"""Helpers for the pre-parse lines queue used by ``_ProcessLineReader`` and ``PtyLineReader``.

The production code keeps ``self._lines_queue`` as a
``BoundedLinesQueue`` (O(1) ``popleft``) but tests inject a raw
``list`` (``O(n)`` ``pop(0)``) to keep their setup cheap. This module
exposes a single ``_pop_queue_line`` helper that dispatches to the
correct primitive at runtime, keeping the readers free of inline
suppression comments and the audit-lint-bypass guard happy.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections import deque


@runtime_checkable
class _PopleftQueue(Protocol):
    def popleft(self) -> str: ...


def _pop_queue_line(queue: _PopleftQueue | list[str] | deque[str]) -> str:
    """Pop the leftmost line from a queue-like ``queue``.

    Accepts either a ``BoundedLinesQueue`` (production) or a plain
    ``list`` (tests). Returns the popped line as ``str``. Raises
    ``IndexError`` when the queue is empty; callers must guard on
    ``if queue`` before invoking this helper.
    """
    if isinstance(queue, _PopleftQueue):
        return queue.popleft()
    return queue.pop(0)


__all__ = ["_pop_queue_line"]
