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

A second repetition dimension (``mark_tool_call``) tracks identical tool-call
fingerprints independently.  An agent wedged in an identical-tool-call retry
loop (e.g. the same ``Bash`` command with the same arguments re-issued N
times without producing forward progress) trips
``WatchdogFireReason.REPEATED_IDENTICAL_TOOL_CALL`` via the same
consecutive + window rules.  The two dimensions are tracked in parallel
deques so a real error-loop and a real tool-call-loop can co-exist without
cancelling each other.

The clock is injected so all timing is deterministic in tests.
"""

from __future__ import annotations

import json
import re
from collections import deque
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

    from ralph.agents.clock import Clock

__all__ = ["RepetitionTracker"]


# Safety floor for the per-dimension deque ``maxlen``.  The window
# rule compares ``_max_fingerprint_count(self._events) >=
# self._window_count`` so the cap MUST be >= ``window_count`` to
# preserve behavior.  256 is a generous floor that bounds memory even
# when ``window_count`` is unset (None -> 0) while leaving plenty of
# room for normal window-rule windows.
_MIN_EVENT_DEQUE_CAP = 256


def _derive_event_maxlen(window_count: int | None) -> int:
    """Compute the per-dimension deque ``maxlen`` from ``window_count``.

    Formula: ``max((window_count or 0) * 8, _MIN_EVENT_DEQUE_CAP)``.

    The 8x safety multiple guarantees the cap is comfortably larger
    than the configured ``window_count`` so the window rule can still
    trip even when noise interleaves between identical fingerprints
    (which inflates the deque length past ``window_count``).  The
    ``_MIN_EVENT_DEQUE_CAP`` floor keeps memory bounded when
    ``window_count`` is unset or small.
    """
    return max((window_count or 0) * 8, _MIN_EVENT_DEQUE_CAP)


# Per-occurrence noise patterns, stripped (in this order) during fingerprinting.
# Applied to the already-lowercased message.
_ISO_TIMESTAMP = re.compile(
    r"\d{4}-\d{2}-\d{2}[t ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:[+-]\d{2}:?\d{2}|z)?"
)
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_LONG_HEX = re.compile(r"\b[0-9a-f]{8,}\b")
# Epoch-scale / id integers (7+ digits). Short codes like 32001 are preserved.
_LONG_INT = re.compile(r"\d{7,}")
_WHITESPACE = re.compile(r"\s+")
_DIAGNOSTIC_PREVIEW_CHARS = 512


def _tool_call_fingerprint(tool_name: str, tool_args: object) -> str:
    """Build a stable fingerprint for ``(tool_name, tool_args)``.

    Args MUST be JSON-serializable.  ``default=str`` falls back to
    ``str(value)`` for non-JSON-serializable values so the fingerprint
    is stable even when the args contain datetime / Path / custom
    objects.  ``sort_keys=True`` ensures dict-key ordering does not
    affect the fingerprint so ``{"a": 1, "b": 2}`` and
    ``{"b": 2, "a": 1}`` produce the same fingerprint.
    """
    try:
        args_blob = json.dumps(tool_args, sort_keys=True, default=str)
    except TypeError:
        # Last-ditch: stringify the args so we still produce a stable
        # fingerprint.  This branch is rare (json.dumps with
        # default=str covers nearly every type) but keeps the
        # fingerprint deterministic under all inputs.
        args_blob = repr(tool_args)
    return f"{tool_name}|{args_blob}"


def _tool_args_preview(tool_args: object) -> str:
    """Return a bounded, stable diagnostic preview for tool arguments."""
    try:
        preview = json.dumps(tool_args, sort_keys=True, default=str)
    except TypeError:
        preview = repr(tool_args)
    if len(preview) > _DIAGNOSTIC_PREVIEW_CHARS:
        return preview[: _DIAGNOSTIC_PREVIEW_CHARS - 3] + "..."
    return preview


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
        # wt-024 M1: bound the per-dimension event deques by a cap
        # derived from ``window_count``.  Previously the deques were
        # unbounded and ``_prune`` / ``_prune_tool`` returned early
        # when ``window_seconds`` was None, so they could grow for the
        # whole watchdog lifetime.  The cap is always >=
        # ``window_count`` so the window rule still trips.
        self._event_maxlen: int = _derive_event_maxlen(window_count)
        # Error / cosmetic-progress dimension (existing).
        # _event_maxlen validated positive
        self._events: deque[tuple[str, float]] = deque(maxlen=self._event_maxlen)  # bounded-accumulator-ok  # type: ignore[var-annotated]  # reason: autogenerated code has no type support, see docs/agents/type-ignore-policy.md#autogenerated-code
        self._last_fingerprint: str | None = None
        self._consecutive = 0
        # Tool-call dimension (new in this PR).  Tracked independently
        # so a real error-loop and a real tool-call-loop can co-exist.
        # per-dimension FIFO, same cap as _events
        self._tool_events: deque[tuple[str, float]] = deque(maxlen=self._event_maxlen)  # bounded-accumulator-ok  # type: ignore[var-annotated]  # reason: autogenerated code has no type support, see docs/agents/type-ignore-policy.md#autogenerated-code
        self._last_tool_fingerprint: str | None = None
        self._last_tool_name: str | None = None
        self._last_tool_args_preview: str | None = None
        self._tool_consecutive = 0

    @property
    def event_buffer_maxlen(self) -> int:
        """Return the per-dimension deque ``maxlen`` cap.

        Read-only, additive, non-breaking.  Exposes the bound so
        callers (and tests) can reason about memory usage and
        assert the cap is in effect.  Both dimensions share the
        same cap derived from ``window_count``.
        """
        return self._event_maxlen

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

    def mark_tool_call(
        self,
        tool_name: str,
        tool_args: object,
    ) -> None:
        """Record one tool-call observation fingerprinted on ``(name, args)``.

        Independent of :meth:`note_error` so an identical tool-call wedge
        (e.g. ``Bash`` with the same arguments re-issued N times) trips
        via the consecutive + window rules on the tool-call dimension
        without requiring the agent to also emit errors.

        Args:
            tool_name: The tool name (e.g. ``"Bash"``).  Empty / None
                tool names are coerced to ``"unknown"`` so the
                fingerprint is always well-formed.
            tool_args: The tool arguments (any JSON-serializable
                structure).  ``None`` is treated as an empty dict.
        """
        name = tool_name or "unknown"
        args = tool_args if tool_args is not None else {}
        fingerprint = _tool_call_fingerprint(name, args)
        now = self._clock.monotonic()
        if fingerprint == self._last_tool_fingerprint:
            self._tool_consecutive += 1
        else:
            self._last_tool_fingerprint = fingerprint
            self._tool_consecutive = 1
        self._last_tool_name = name
        self._last_tool_args_preview = _tool_args_preview(args)
        self._tool_events.append((fingerprint, now))
        self._prune_tool(now)

    def note_progress(self) -> None:
        """Record genuine forward progress; clears both trip conditions."""
        self._consecutive = 0
        self._last_fingerprint = None
        self._events.clear()
        self._tool_consecutive = 0
        self._last_tool_fingerprint = None
        self._last_tool_name = None
        self._last_tool_args_preview = None
        self._tool_events.clear()

    def diagnostic(self) -> dict[str, str | int]:
        """Return bounded diagnostic context for any active repetition streak."""
        diagnostic: dict[str, str | int] = {}
        if self._last_fingerprint is not None:
            diagnostic["error_fingerprint"] = self._last_fingerprint
            diagnostic["error_consecutive"] = self._consecutive
        if self._last_tool_name is not None:
            diagnostic["tool_name"] = self._last_tool_name
            diagnostic["tool_args_preview"] = self._last_tool_args_preview or "{}"
            diagnostic["tool_consecutive"] = self._tool_consecutive
        return diagnostic

    def tripped(self) -> bool:
        """Return True when either trip condition is currently satisfied.

        Consults BOTH the error / cosmetic-progress dimension AND the
        tool-call dimension.  Either dimension tripping causes the
        watchdog to fire ``REPEATED_ERROR_LOOP`` (or
        ``REPEATED_IDENTICAL_TOOL_CALL`` based on which dimension
        tripped first).
        """
        if self._error_dimension_tripped():
            return True
        return self._tool_dimension_tripped()

    def tripped_tool_dimension(self) -> bool:
        """Return True when ONLY the tool-call dimension is tripped.

        Convenience accessor for the watchdog's ``evaluate`` method so
        it can emit ``REPEATED_IDENTICAL_TOOL_CALL`` rather than
        ``REPEATED_ERROR_LOOP`` when only the tool-call wedge is
        active.  Returns False when the error dimension is also
        tripped (the error reason wins).
        """
        return self._tool_dimension_tripped() and not self._error_dimension_tripped()

    def _error_dimension_tripped(self) -> bool:
        if (
            self._consecutive_threshold is not None
            and self._consecutive >= self._consecutive_threshold
        ):
            return True
        if self._window_count is not None and self._window_seconds is not None:
            now = self._clock.monotonic()
            self._prune(now)
            if _max_fingerprint_count(self._events) >= self._window_count:
                return True
        return False

    def _tool_dimension_tripped(self) -> bool:
        if (
            self._consecutive_threshold is not None
            and self._tool_consecutive >= self._consecutive_threshold
        ):
            return True
        if self._window_count is not None and self._window_seconds is not None:
            now = self._clock.monotonic()
            self._prune_tool(now)
            if _max_fingerprint_count(self._tool_events) >= self._window_count:
                return True
        return False

    def _prune(self, now: float) -> None:
        if self._window_seconds is None:
            return
        cutoff = now - self._window_seconds
        while self._events and self._events[0][1] < cutoff:
            self._events.popleft()

    def _prune_tool(self, now: float) -> None:
        if self._window_seconds is None:
            return
        cutoff = now - self._window_seconds
        while self._tool_events and self._tool_events[0][1] < cutoff:
            self._tool_events.popleft()


def _max_fingerprint_count(events: Iterable[tuple[str, float]]) -> int:
    counts: dict[str, int] = {}
    top = 0
    for fingerprint, _ in events:
        running = counts.get(fingerprint, 0) + 1
        counts[fingerprint] = running
        top = max(top, running)
    return top
