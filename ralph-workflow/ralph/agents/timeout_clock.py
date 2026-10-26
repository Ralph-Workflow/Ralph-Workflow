"""Fake test clock for the agent timeout subsystem."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from ralph.agents.clock import Clock
from ralph.agents.system_clock import SystemClock

if TYPE_CHECKING:
    import threading

__all__ = ["Clock", "FakeClock", "SystemClock"]


class FakeClock:
    """Test Clock: advances logical time synchronously without real waits."""

    def __init__(
        self,
        start: float = 0.0,
        *,
        wall_time: datetime | None = None,
        epoch_start: float | None = None,
    ) -> None:
        self._now: float = start
        if wall_time is not None:
            effective_dt = (
                wall_time if wall_time.tzinfo is not None else wall_time.replace(tzinfo=UTC)
            )
            self._epoch_offset: float = effective_dt.timestamp() - start
        elif epoch_start is not None:
            self._epoch_offset = epoch_start - start
        else:
            self._epoch_offset = 0.0

    def monotonic(self) -> float:
        return self._now

    def epoch(self) -> float:
        return self._now + self._epoch_offset

    def now_utc(self) -> datetime:
        return datetime.fromtimestamp(self.epoch(), tz=UTC)

    def sleep(self, seconds: float) -> None:
        self._now += seconds

    def advance(self, seconds: float) -> None:
        """Advance logical time by seconds without blocking."""
        self._now += seconds

    def wait_for_event(self, event: threading.Event, seconds: float) -> bool:
        self._now += seconds
        return event.is_set()

    def set_wall_time(self, dt: datetime) -> None:
        """Set current wall time."""
        effective_dt = dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)
        self._epoch_offset = effective_dt.timestamp() - self._now

    def set_epoch(self, seconds: float) -> None:
        """Set current epoch timestamp."""
        self._epoch_offset = seconds - self._now
