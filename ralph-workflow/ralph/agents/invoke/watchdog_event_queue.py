from __future__ import annotations

from contextlib import suppress
from queue import Empty, Full, Queue
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable


class DroppingEventQueue(Queue[object]):
    def __init__(self, maxsize: int) -> None:
        super().__init__(maxsize=maxsize)
        self._on_overflow: Callable[[], None] | None = None

    def set_overflow_callback(self, callback: Callable[[], None]) -> None:
        self._on_overflow = callback

    def put(self, item: object, block: bool = True, timeout: float | None = None) -> None:
        del block, timeout
        try:
            super().put(item, block=False)
            return
        except Full:
            pass
        try:
            super().get(block=False)
            super().task_done()
        except Empty:
            pass
        with suppress(Full):
            super().put(item, block=False)
        if self._on_overflow is not None:
            self._on_overflow()
