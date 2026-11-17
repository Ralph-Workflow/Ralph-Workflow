from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from ralph.agents.invoke.watchdog_event_queue import DroppingEventQueue

if TYPE_CHECKING:
    from queue import Queue

MAX_WATCHDOG_EVENT_QUEUE = 4096


class _WatchObserverProtocol(Protocol):
    def schedule(self, event_handler: object, path: str, **kwargs: object) -> None: ...

    def start(self) -> None: ...

    def stop(self) -> None: ...

    def join(self, timeout: float | None = None) -> None: ...


@runtime_checkable
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
    observer = Observer()
    if not isinstance(observer, ObserverProtocol):
        return None
    observer._event_queue = DroppingEventQueue(MAX_WATCHDOG_EVENT_QUEUE)
    return observer
