from __future__ import annotations

import threading
from collections.abc import Callable
from typing import TypeVar

FlagsT = TypeVar("FlagsT")


class CancelFlagBox[FlagsT]:
    __slots__ = ("flags", "lock")

    def __init__(
        self,
        *,
        flags: FlagsT,
        lock_factory: Callable[[], threading.Lock] | None = None,
    ) -> None:
        factory = threading.Lock if lock_factory is None else lock_factory
        self.flags = flags
        self.lock = factory()


class LockBox:
    __slots__ = ("lock",)

    def __init__(self, *, lock_factory: Callable[[], threading.Lock] | None = None) -> None:
        factory = threading.Lock if lock_factory is None else lock_factory
        self.lock = factory()


class EventBox:
    __slots__ = ("event",)

    def __init__(self, *, event_factory: Callable[[], threading.Event] | None = None) -> None:
        factory = threading.Event if event_factory is None else event_factory
        self.event = factory()


__all__ = [
    "CancelFlagBox",
    "EventBox",
    "LockBox",
]
