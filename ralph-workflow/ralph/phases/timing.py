"""Phase timing utilities.

Provides monotonic-clock helpers and two dataclasses for measuring how long
each pipeline phase takes:

- ``PhaseTimer`` - start timing a phase with ``PhaseTimer.start(phase)`` and
  stop it with ``timer.finish()`` to get a ``PhaseTimingRecord``.
- ``PhaseTimingRecord`` - frozen record holding the phase name, iteration
  number, start timestamp, and elapsed ``timedelta`` / whole-second count.

All time values use ``time.monotonic`` so they are safe across system-clock
adjustments. Elapsed seconds are truncated (not rounded) to whole integers.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from time import monotonic

from ralph.phases.phase_timing_record import PhaseTimingRecord


def capture_time() -> float:
    """Return a monotonic timestamp suitable for elapsed-time calculations."""
    return monotonic()


def elapsed(start: float) -> timedelta:
    """Return the duration since ``start`` using a monotonic clock."""
    return timedelta(seconds=max(0.0, monotonic() - start))


def elapsed_seconds(start: float) -> int:
    """Return whole elapsed seconds since ``start``."""
    return int(elapsed(start).total_seconds())


@dataclass(frozen=True)
class PhaseTimer:
    """Simple helper for measuring phase execution durations."""

    phase: str
    iteration: int
    started_at: float

    @classmethod
    def start(cls, phase: str, *, iteration: int = 0) -> PhaseTimer:
        """Start timing a phase."""
        return cls(phase=phase, iteration=iteration, started_at=capture_time())

    def finish(self) -> PhaseTimingRecord:
        """Return a completed timing record for the phase."""
        duration = elapsed(self.started_at)
        return PhaseTimingRecord(
            phase=self.phase,
            iteration=self.iteration,
            started_at=self.started_at,
            elapsed=duration,
            elapsed_seconds=int(duration.total_seconds()),
        )


__all__ = [
    "PhaseTimer",
    "PhaseTimingRecord",
    "capture_time",
    "elapsed",
    "elapsed_seconds",
]
