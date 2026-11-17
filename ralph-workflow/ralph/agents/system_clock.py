from __future__ import annotations

import threading
import time
from datetime import UTC, datetime

from ralph.agents.clock import Clock

__all__ = ["SystemClock"]


class SystemClock(Clock):
    _offset: float = 0.0

    def monotonic(self) -> float:
        return time.monotonic() + self._offset

    def now_utc(self) -> datetime:
        return datetime.now(UTC)

    def epoch(self) -> float:
        return time.time() + self._offset

    def sleep(self, seconds: float) -> None:
        threading.Event().wait(seconds)

    def advance(self, seconds: float) -> None:
        self._offset += seconds

    def wait_for_event(self, event: threading.Event, seconds: float) -> bool:
        return event.wait(seconds)
