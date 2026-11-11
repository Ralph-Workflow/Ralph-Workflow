
from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    import threading
    from datetime import datetime

__all__ = ["Clock"]


@runtime_checkable
class Clock(Protocol):

    def monotonic(self) -> float:
        ...

    def now_utc(self) -> datetime:
        ...

    def epoch(self) -> float:
        ...

    def sleep(self, seconds: float) -> None:
        ...

    def wait_for_event(self, event: threading.Event, seconds: float) -> bool:
        ...
