
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from time import monotonic

from ralph.phases.phase_timing_record import PhaseTimingRecord


def capture_time() -> float:
    return monotonic()


def elapsed(start: float) -> timedelta:
    return timedelta(seconds=max(0.0, monotonic() - start))


def elapsed_seconds(start: float) -> int:
    return int(elapsed(start).total_seconds())


@dataclass(frozen=True)
class PhaseTimer:

    phase: str
    iteration: int
    started_at: float

    @classmethod
    def start(cls, phase: str, *, iteration: int = 0) -> PhaseTimer:
        return cls(phase=phase, iteration=iteration, started_at=capture_time())

    def finish(self) -> PhaseTimingRecord:
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
