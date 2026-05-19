"""Production clock for the agent timeout subsystem."""

from __future__ import annotations

import threading
import time

from ralph.agents.clock import Clock

__all__ = ["SystemClock"]


class SystemClock(Clock):
    """Production Clock: uses real wall-clock time."""

    def monotonic(self) -> float:
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        # threading.Event().wait is interruptible (preserves SIGINT semantics)
        # and matches the existing lines_event.wait() pattern in invoke.py.
        threading.Event().wait(seconds)

    def wait_for_event(self, event: threading.Event, seconds: float) -> bool:
        return event.wait(seconds)
