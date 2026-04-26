"""Injectable Clock seam for the agent idle timeout subsystem.

Production code uses SystemClock; tests use FakeClock so timeout behavior can be
exercised deterministically without real wall-clock waits per CLAUDE.md test
performance policy.
"""

from __future__ import annotations

import threading
import time
from typing import Protocol, runtime_checkable

__all__ = ["Clock", "FakeClock", "SystemClock"]


@runtime_checkable
class Clock(Protocol):
    """Protocol for wall-clock operations used by the timeout subsystem."""

    def monotonic(self) -> float:
        """Return current monotonic time in seconds."""
        ...  # pragma: no cover

    def sleep(self, seconds: float) -> None:
        """Pause execution for the given number of seconds."""
        ...  # pragma: no cover


class SystemClock:
    """Production Clock: uses real wall-clock time."""

    def monotonic(self) -> float:
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        # threading.Event().wait is interruptible (preserves SIGINT semantics)
        # and matches the existing lines_event.wait() pattern in invoke.py.
        threading.Event().wait(seconds)


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
