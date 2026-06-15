"""Repetition tracker for the agent stuck-loop circuit breaker.

Tracks how often an agent re-emits the *same* error (or repeats the *same*
cosmetic progress status) without making forward progress. The idle watchdog
consults it to fire ``REPEATED_ERROR_LOOP`` when an agent is wedged in a
retry storm — the failure mode behind a run that logged the identical
``MCP error -32001: Request timed out`` every ~34s for ~5 hours.

Two independent trip conditions (either fires):

- **consecutive**: ``consecutive_threshold`` identical fingerprints in a row
  with no intervening :meth:`note_progress`.
- **window**: ``window_count`` occurrences of one fingerprint within the
  trailing ``window_seconds`` — catches the case where a tiny bit of cosmetic
  output interleaves between errors and keeps resetting the consecutive streak.

Fingerprinting collapses per-occurrence noise (ISO timestamps, UUIDs, long hex
ids, epoch-scale integers) so the same underlying error matches across
occurrences, while stable signal such as a ``-32001`` error code survives.

The clock is injected so all timing is deterministic in tests.
"""

from __future__ import annotations

import re
from collections import deque
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

    from ralph.agents.clock import Clock

__all__ = ["RepetitionTracker"]


# Per-occurrence noise patterns, stripped (in this order) during fingerprinting.
# Applied to the already-lowercased message.
_ISO_TIMESTAMP = re.compile(
    r"\d{4}-\d{2}-\d{2}[t ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:[+-]\d{2}:?\d{2}|z)?"
)
_UUID = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
)
_LONG_HEX = re.compile(r"\b[0-9a-f]{8,}\b")
# Epoch-scale / id integers (7+ digits). Short codes like 32001 are preserved.
_LONG_INT = re.compile(r"\d{7,}")
_WHITESPACE = re.compile(r"\s+")


class RepetitionTracker:
    """Counts repeated same-fingerprint events to detect a wedged retry loop."""

    def __init__(
        self,
        clock: Clock,
        *,
        consecutive_threshold: int | None,
        window_count: int | None,
        window_seconds: float | None,
    ) -> None:
        self._clock = clock
        self._consecutive_threshold = consecutive_threshold
        self._window_count = window_count
        self._window_seconds = window_seconds
        self._events: deque[tuple[str, float]] = deque()
        self._last_fingerprint: str | None = None
        self._consecutive = 0

    @staticmethod
    def fingerprint(message: str) -> str:
        """Normalize a message so equivalent occurrences collapse to one key."""
        text = message.lower()
        text = _ISO_TIMESTAMP.sub("<ts>", text)
        text = _UUID.sub("<uuid>", text)
        text = _LONG_HEX.sub("<hex>", text)
        text = _LONG_INT.sub("<n>", text)
        return _WHITESPACE.sub(" ", text).strip()

    def note_error(self, message: str) -> None:
        """Record one error/repeat occurrence, fingerprinted."""
        fingerprint = self.fingerprint(message)
        now = self._clock.monotonic()
        if fingerprint == self._last_fingerprint:
            self._consecutive += 1
        else:
            self._last_fingerprint = fingerprint
            self._consecutive = 1
        self._events.append((fingerprint, now))
        self._prune(now)

    def note_progress(self) -> None:
        """Record genuine forward progress; clears both trip conditions."""
        self._consecutive = 0
        self._last_fingerprint = None
        self._events.clear()

    def tripped(self) -> bool:
        """Return True when either trip condition is currently satisfied."""
        if (
            self._consecutive_threshold is not None
            and self._consecutive >= self._consecutive_threshold
        ):
            return True
        if self._window_count is not None and self._window_seconds is not None:
            now = self._clock.monotonic()
            self._prune(now)
            # Count the MOST-repeated fingerprint in the window (not just the most
            # recent one): a single interleaved distinct line must not be able to
            # mask a still-active error loop.
            if _max_fingerprint_count(self._events) >= self._window_count:
                return True
        return False

    def _prune(self, now: float) -> None:
        if self._window_seconds is None:
            return
        cutoff = now - self._window_seconds
        while self._events and self._events[0][1] < cutoff:
            self._events.popleft()


def _max_fingerprint_count(events: Iterable[tuple[str, float]]) -> int:
    counts: dict[str, int] = {}
    top = 0
    for fingerprint, _ in events:
        running = counts.get(fingerprint, 0) + 1
        counts[fingerprint] = running
        top = max(top, running)
    return top
