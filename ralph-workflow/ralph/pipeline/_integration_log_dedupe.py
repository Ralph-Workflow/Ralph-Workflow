"""Outcome-once logging with count and rate limit for the integration owner path.

A held planning gate or an unlanded integration recurs on every retry tick,
and each recurrence used to print its own CRITICAL line -- one line per
retry, for hours, burying the log and hiding the one fact that matters: the
outcome did not change. The rule this module owns (log-spam rule,
observability catalogue F):

- the first occurrence of an outcome is logged once, with the next action;
- identical repeats are counted, not printed;
- one rate-limited summary line per interval carries the running count;
- a changed outcome is a different message and logs immediately.

Time arrives through an injectable monotonic clock, so no test ever sleeps.
The map is bounded: at most ``max_entries`` distinct outcomes are retained,
evicted oldest-first, so a long unattended run cannot grow memory per
distinct line.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from typing import Protocol


class OutcomeEmit(Protocol):
    """Sink for a user-visible outcome line."""

    def __call__(self, message: str) -> None: ...


class MonotonicClock(Protocol):
    """Injectable monotonic clock (seconds)."""

    def __call__(self) -> float: ...


class IntegrationOutcomeLog:
    """Dedupe and rate-limit repeated identical integration outcomes.

    ``record`` is keyed on the message itself: an identical line IS the
    identical outcome. The first occurrence always emits; repeats within
    ``repeat_log_interval_seconds`` are silent and counted; the first
    repeat after the interval emits one summary line carrying the
    suppressed count, then counting restarts from that line.

    Thread-safe: the catch-up ticker logs from a background thread while
    the run loop logs from the main thread.
    """

    def __init__(
        self,
        *,
        clock: MonotonicClock = time.monotonic,
        repeat_log_interval_seconds: float = 300.0,
        max_entries: int = 64,
    ) -> None:
        if repeat_log_interval_seconds <= 0:
            raise ValueError("repeat_log_interval_seconds must be positive")
        if max_entries <= 0:
            raise ValueError("max_entries must be positive")
        self._clock = clock
        self._repeat_log_interval_seconds = repeat_log_interval_seconds
        self._max_entries = max_entries
        # OrderedDict + count cap: _evict_oldest enforces the max_entries
        # bound (oldest-first popitem), so the map cannot grow past the cap.
        self._records: OrderedDict[str, tuple[float, int]] = (
            OrderedDict()
        )  # bounded-accumulator-ok: count cap = max_entries via _evict_oldest
        self._lock = threading.Lock()

    def record(self, emit: OutcomeEmit, message: str) -> bool:
        """Record one occurrence of ``message``; emit only when the user must see it.

        Returns True exactly when a line was emitted. Emission happens
        outside the lock so a slow sink cannot block the other thread's
        accounting.
        """
        now = self._clock()
        summary: str | None = None
        with self._lock:
            entry = self._records.get(message)
            if entry is None:
                self._records[message] = (now, 1)
                self._records.move_to_end(message)
                self._evict_oldest()
                summary = message
            else:
                last_emitted_at, count = entry
                repeats = count + 1
                if now - last_emitted_at < self._repeat_log_interval_seconds:
                    self._records[message] = (last_emitted_at, repeats)
                    return False
                self._records[message] = (now, 1)
                self._records.move_to_end(message)
                summary = f"{message} (repeated {repeats}x since the last update)"
        emit(summary)
        return True

    def _evict_oldest(self) -> None:
        while len(self._records) > self._max_entries:
            self._records.popitem(last=False)


_default_outcome_log = IntegrationOutcomeLog()


def integration_outcome_log() -> IntegrationOutcomeLog:
    """Return the process-wide outcome log shared by every integration seam.

    One shared instance means every seam that logs through it inherits
    the same dedupe: a planning-gate line and a retry-wait line for the
    same outcome share one count instead of each spamming independently.
    """
    return _default_outcome_log


__all__ = ["IntegrationOutcomeLog", "integration_outcome_log"]
