from __future__ import annotations

import importlib
from collections.abc import Callable, Iterable
from typing import TYPE_CHECKING, Protocol, cast, runtime_checkable

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

    class _WatchdogObserversModule(Protocol):
        Observer: type[ObserverProtocol]

    try:
        observers_module = cast(
            "_WatchdogObserversModule", importlib.import_module("watchdog.observers")
        )
    except ImportError:
        return None
    observer = observers_module.Observer()
    observer._event_queue = DroppingEventQueue(MAX_WATCHDOG_EVENT_QUEUE)
    return observer


MAX_OBSERVER_RESTARTS = 5
OBSERVER_RESTART_BASE_SECONDS = 0.5
OBSERVER_RESTART_MAX_SECONDS = 30.0

OBSERVER_HEALTH_INTERVAL_SECONDS = 0.5


def observer_restart_delay_seconds(restarts: int) -> float:
    return min(OBSERVER_RESTART_BASE_SECONDS * float(1 << restarts), OBSERVER_RESTART_MAX_SECONDS)


@runtime_checkable
class _LiveThread(Protocol):
    def is_alive(self) -> bool: ...


def observer_threads(observer: object) -> tuple[_LiveThread, ...]:
    threads: list[_LiveThread] = []
    if isinstance(observer, _LiveThread):
        threads.append(observer)
    emitters = attr_or_none(observer, "emitters")
    if isinstance(emitters, (set, frozenset, list, tuple)):
        for emitter in cast("Iterable[object]", emitters):
            if isinstance(emitter, _LiveThread):
                threads.append(emitter)
            buffer = attr_or_none(emitter, "_inotify")
            if isinstance(buffer, _LiveThread):
                threads.append(buffer)
    return tuple(threads)


def observer_has_dead_thread(observer: object) -> bool:
    return any(not thread.is_alive() for thread in observer_threads(observer))
