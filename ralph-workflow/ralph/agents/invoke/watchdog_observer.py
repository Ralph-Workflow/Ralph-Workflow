"""Construct optional watchdog observers with bounded event ingestion."""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Protocol, cast

from ralph.agents.invoke.watchdog_event_queue import DroppingEventQueue

if TYPE_CHECKING:
    from queue import Queue

MAX_WATCHDOG_EVENT_QUEUE = 4096


class ObserverProtocol(Protocol):
    _event_queue: Queue[object]

    @property
    def event_queue(self) -> Queue[object]: ...
    def schedule(self, event_handler: object, path: str, **kwargs: object) -> None: ...
    def start(self) -> None: ...
    def stop(self) -> None: ...
    def join(self, timeout: float | None = None) -> None: ...


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
