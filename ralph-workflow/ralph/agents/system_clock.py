from __future__ import annotations

import threading
import time
from datetime import UTC, datetime

from ralph.agents.clock import Clock

__all__ = ["SystemClock"]


class SystemClock(Clock):
    def monotonic(self) -> float:
        return time.monotonic()

    def now_utc(self) -> datetime:
        return datetime.now(UTC)

    def epoch(self) -> float:
        return time.time()

    def sleep(self, seconds: float) -> None:
        threading.Event().wait(seconds)

    def wait_for_event(self, event: threading.Event, seconds: float) -> bool:
        return event.wait(seconds)
