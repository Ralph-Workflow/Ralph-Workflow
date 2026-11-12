from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections import deque


@runtime_checkable
class _PopleftQueue(Protocol):
    def popleft(self) -> str: ...


def _pop_queue_line(queue: _PopleftQueue | list[str] | deque[str]) -> str:
    if isinstance(queue, _PopleftQueue):
        return queue.popleft()
    return queue.pop(0)


__all__ = ["_pop_queue_line"]
