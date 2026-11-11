
from __future__ import annotations

import json
import re
from collections import deque
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

    from ralph.agents.clock import Clock

__all__ = ["RepetitionTracker"]


_MIN_EVENT_DEQUE_CAP = 256


def _derive_event_maxlen(window_count: int | None) -> int:
    return max((window_count or 0) * 8, _MIN_EVENT_DEQUE_CAP)


_ISO_TIMESTAMP = re.compile(
    r"\d{4}-\d{2}-\d{2}[t ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:[+-]\d{2}:?\d{2}|z)?"
)
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_LONG_HEX = re.compile(r"\b[0-9a-f]{8,}\b")
_LONG_INT = re.compile(r"\d{7,}")
_WHITESPACE = re.compile(r"\s+")
_DIAGNOSTIC_PREVIEW_CHARS = 512


def _tool_call_fingerprint(tool_name: str, tool_args: object) -> str:
    try:
        args_blob = json.dumps(tool_args, sort_keys=True, default=str)
    except TypeError:
        args_blob = repr(tool_args)
    return f"{tool_name}|{args_blob}"


def _tool_args_preview(tool_args: object) -> str:
    try:
        preview = json.dumps(tool_args, sort_keys=True, default=str)
    except TypeError:
        preview = repr(tool_args)
    if len(preview) > _DIAGNOSTIC_PREVIEW_CHARS:
        return preview[: _DIAGNOSTIC_PREVIEW_CHARS - 3] + "..."
    return preview


class RepetitionTracker:

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
        self._event_maxlen: int = _derive_event_maxlen(window_count)
        self._events: deque[tuple[str, float]] = deque(
            maxlen=self._event_maxlen
        )
        self._last_fingerprint: str | None = None
        self._consecutive = 0
        self._tool_events: deque[tuple[str, float]] = deque(
            maxlen=self._event_maxlen
        )
        self._last_tool_fingerprint: str | None = None
        self._last_tool_name: str | None = None
        self._last_tool_args_preview: str | None = None
        self._tool_consecutive = 0

    @property
    def event_buffer_maxlen(self) -> int:
        return self._event_maxlen

    @staticmethod
    def fingerprint(message: str) -> str:
        text = message.lower()
        text = _ISO_TIMESTAMP.sub("<ts>", text)
        text = _UUID.sub("<uuid>", text)
        text = _LONG_HEX.sub("<hex>", text)
        text = _LONG_INT.sub("<n>", text)
        return _WHITESPACE.sub(" ", text).strip()

    def note_error(self, message: str) -> None:
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
        self._consecutive = 0
        self._last_fingerprint = None
        self._tool_consecutive = 0
        self._last_tool_fingerprint = None
        self._last_tool_name = None
        self._last_tool_args_preview = None

    def diagnostic(self) -> dict[str, str | int]:
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
        if self._error_dimension_tripped():
            return True
        return self._tool_dimension_tripped()

    def tripped_tool_dimension(self) -> bool:
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
            repeats = _max_fingerprint_count(self._tool_events)
            if repeats >= self._window_count and _window_is_cycling(self._tool_events):
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


_MAX_CYCLE_FINGERPRINTS = 4


def _window_is_cycling(events: Iterable[tuple[str, float]]) -> bool:
    distinct: set[str] = set()
    for fingerprint, _ in events:
        distinct.add(fingerprint)
        if len(distinct) > _MAX_CYCLE_FINGERPRINTS:
            return False
    return True


def _max_fingerprint_count(events: Iterable[tuple[str, float]]) -> int:
    counts: dict[str, int] = {}
    top = 0
    for fingerprint, _ in events:
        running = counts.get(fingerprint, 0) + 1
        counts[fingerprint] = running
        top = max(top, running)
    return top
