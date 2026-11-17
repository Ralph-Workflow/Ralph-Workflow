from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Protocol, cast

from ralph._typed_attrs import attr_or_none
from ralph.agents.invoke.watchdog_event_queue import DroppingEventQueue

if TYPE_CHECKING:
    from queue import Queue

MAX_WATCHDOG_EVENT_QUEUE = 4096


class _WatchObserverProtocol(Protocol):
    def schedule(self, event_handler: object, path: str, **kwargs: object) -> None: ...

    def start(self) -> None: ...

    def stop(self) -> None: ...

    def join(self, timeout: float | None = None) -> None: ...


class ObserverProtocol(Protocol):
    _event_queue: Queue[object]

    @property
    def event_queue(self) -> Queue[object]: ...
    def schedule(self, event_handler: object, path: str, **kwargs: object) -> None: ...
    def start(self) -> None: ...
    def stop(self) -> None: ...
    def join(self, timeout: float | None = None) -> None: ...


WatchObserverFactory = Callable[[], _WatchObserverProtocol | None]


def create_watchdog_observer() -> ObserverProtocol | None:
    try:
        from watchdog.observers import Observer
    except ImportError:
        return None
    raw: object = Observer()
    object.__setattr__(raw, "_event_queue", DroppingEventQueue(MAX_WATCHDOG_EVENT_QUEUE))
    schedule = attr_or_none(raw, "schedule")
    start = attr_or_none(raw, "start")
    stop = attr_or_none(raw, "stop")
    join = attr_or_none(raw, "join")
    if not (
        callable(schedule)
        and callable(start)
        and callable(stop)
        and callable(join)
        and hasattr(raw, "event_queue")
    ):
        return None
    return cast("ObserverProtocol", raw)
