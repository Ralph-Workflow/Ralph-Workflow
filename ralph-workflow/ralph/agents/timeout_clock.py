"""Fake test clock for the agent timeout subsystem."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.agents.clock import Clock
from ralph.agents.system_clock import SystemClock

if TYPE_CHECKING:
    import threading

__all__ = ["Clock", "FakeClock", "SystemClock"]


class FakeClock:
    """Test Clock: advances logical time synchronously without real waits."""

    def __init__(self, start: float = 0.0) -> None:
        self._now: float = start

    def monotonic(self) -> float:
        return self._now

    def sleep(self, seconds: float) -> None:
        self._now += seconds

    def advance(self, seconds: float) -> None:
        """Advance logical time by seconds without blocking."""
        self._now += seconds

    def wait_for_event(self, event: threading.Event, seconds: float) -> bool:
        self._now += seconds
        return event.is_set()
