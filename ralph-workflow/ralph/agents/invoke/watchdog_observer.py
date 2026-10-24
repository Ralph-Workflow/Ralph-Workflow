"""Construct optional watchdog observers with bounded event ingestion."""

from __future__ import annotations

import importlib
from collections.abc import Callable
from typing import TYPE_CHECKING, Protocol, cast

from ralph.agents.invoke.watchdog_event_queue import DroppingEventQueue

if TYPE_CHECKING:
    from queue import Queue

MAX_WATCHDOG_EVENT_QUEUE = 4096


class _WatchObserverProtocol(Protocol):
    """Describe the watchdog observer lifecycle the workspace monitor drives.

    The workspace monitor owns exactly one observer and drives four
    members on it: ``schedule`` (the single recursive root watch),
    ``start``, ``stop`` and ``join``. This is the whole surface an
    injected observer has to provide, so a caller can supply a double
    that implements the lifecycle without the watchdog event queue the
    production observer carries.
    """

    def schedule(self, event_handler: object, path: str, **kwargs: object) -> None:
        """Register one watch for ``path``."""
        ...

    def start(self) -> None:
        """Begin delivering events for the registered watches."""
        ...

    def stop(self) -> None:
        """Stop delivering events for the registered watches."""
        ...

    def join(self, timeout: float | None = None) -> None:
        """Wait for the observer's delivery thread to finish."""
        ...


class ObserverProtocol(Protocol):
    """Describe the watchdog observer surface used by workspace monitoring."""

    _event_queue: Queue[object]

    @property
    def event_queue(self) -> Queue[object]: ...
    def schedule(self, event_handler: object, path: str, **kwargs: object) -> None: ...
    def start(self) -> None: ...
    def stop(self) -> None: ...
    def join(self, timeout: float | None = None) -> None: ...


#: Creates the observer a workspace watch registers its single recursive
#: root watch with. ``None`` means "no observer is available".
WatchObserverFactory = Callable[[], _WatchObserverProtocol | None]


def create_watchdog_observer() -> ObserverProtocol | None:
    """Create a watchdog observer with a bounded queue when watchdog is installed."""

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
