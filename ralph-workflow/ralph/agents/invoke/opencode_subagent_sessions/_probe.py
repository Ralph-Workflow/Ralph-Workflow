"""Poll the child sessions of one OpenCode parent and forward their work."""

from __future__ import annotations

import time
from collections import OrderedDict
from typing import TYPE_CHECKING

from loguru import logger

from ._child_part import OpenCodeChildPart, summarize_child_part

if TYPE_CHECKING:
    from collections.abc import Callable

    from ._part_source import OpenCodeChildPartSource

#: Dedupe window for ``(part_id, time_updated)`` pairs already forwarded.
_MAX_SEEN_PARTS: int = 512
#: Cache of child session ids already announced to the operator log.
_MAX_KNOWN_CHILDREN: int = 64


class OpenCodeSubagentSessionProbe:
    """Poll the child sessions of one OpenCode parent and forward their work.

    Each newly updated part reaches two sinks: ``subagent_sink`` receives the
    operator-readable summary (the watchdog's ``record_subagent_work``
    channel) and ``child_progress_sink`` receives the child session id (the
    execution strategy's child-liveness registry). Polling is throttled to
    ``poll_interval_seconds`` so calling :meth:`poll` on every reader tick
    stays cheap; the store itself is queried only once a parent session id
    has been captured from the stream.
    """

    def __init__(
        self,
        *,
        source: OpenCodeChildPartSource,
        parent_session_id: Callable[[], str | None],
        subagent_sink: Callable[[str], None],
        child_progress_sink: Callable[[str], None],
        monotonic: Callable[[], float] = time.monotonic,
        wall_clock_ms: Callable[[], int] | None = None,
        poll_interval_seconds: float = 2.0,
        status_interval_seconds: float = 30.0,
    ) -> None:
        self._source = source
        self._parent_session_id = parent_session_id
        self._subagent_sink = subagent_sink
        self._child_progress_sink = child_progress_sink
        self._monotonic = monotonic
        wall = wall_clock_ms or _default_wall_clock_ms
        # Only parts written after this invocation started count: a resumed
        # session may own finished children from an earlier invocation.
        self._since_ms: int = wall()
        self._poll_interval = poll_interval_seconds
        self._status_interval = status_interval_seconds
        self._last_poll_at: float | None = None
        self._last_status_at: float | None = None
        self._updates_since_status = 0
        self._latest_summary: str | None = None
        self._seen: OrderedDict[tuple[str, int], None] = (
            OrderedDict()
        )  # bounded-accumulator-ok: capped at _MAX_SEEN_PARTS
        self._known_children: OrderedDict[str, None] = (
            OrderedDict()
        )  # bounded-accumulator-ok: capped at _MAX_KNOWN_CHILDREN
        self._closed = False

    def poll(self) -> int:
        """Forward every new child part; return how many were forwarded."""
        if self._closed:
            return 0
        now = self._monotonic()
        if self._last_poll_at is not None and now - self._last_poll_at < self._poll_interval:
            return 0
        self._last_poll_at = now
        parent_id = self._parent_session_id()
        if not parent_id:
            return 0
        try:
            parts = self._source.fetch(parent_id, self._since_ms)
        except Exception:
            logger.debug("opencode subagent probe: store read failed (suppressed)")
            return 0
        forwarded = 0
        for part in parts:
            key = (part.part_id, part.time_updated_ms)
            if key in self._seen:
                continue
            self._remember_seen(key)
            self._since_ms = max(self._since_ms, part.time_updated_ms)
            self._announce_child(part)
            summary = summarize_child_part(part)
            self._latest_summary = summary
            self._forward(part.child_session_id, summary)
            forwarded += 1
        self._updates_since_status += forwarded
        self._maybe_log_status(now)
        return forwarded

    def close(self) -> None:
        """Release the underlying source; further polls are no-ops."""
        if self._closed:
            return
        self._closed = True
        try:
            self._source.close()
        except Exception:
            logger.debug("opencode subagent probe: source close failed (suppressed)")

    def _remember_seen(self, key: tuple[str, int]) -> None:
        self._seen[key] = None
        while len(self._seen) > _MAX_SEEN_PARTS:
            self._seen.popitem(last=False)

    def _announce_child(self, part: OpenCodeChildPart) -> None:
        if part.child_session_id in self._known_children:
            return
        self._known_children[part.child_session_id] = None
        while len(self._known_children) > _MAX_KNOWN_CHILDREN:
            self._known_children.popitem(last=False)
        logger.info(
            "opencode subagent started: agent={} title={!r} session={}",
            part.agent or "?",
            part.title,
            part.child_session_id,
        )

    def _forward(self, child_session_id: str, summary: str) -> None:
        try:
            self._child_progress_sink(child_session_id)
        except Exception:
            logger.debug("opencode subagent probe: child progress sink raised (suppressed)")
        try:
            self._subagent_sink(summary)
        except Exception:
            logger.debug("opencode subagent probe: subagent sink raised (suppressed)")

    def _maybe_log_status(self, now: float) -> None:
        if self._updates_since_status == 0:
            return
        if self._last_status_at is not None and now - self._last_status_at < self._status_interval:
            return
        self._last_status_at = now
        logger.info(
            "opencode subagent working: {} store update(s) across {} child session(s); latest={}",
            self._updates_since_status,
            len(self._known_children),
            self._latest_summary,
        )
        self._updates_since_status = 0


def _default_wall_clock_ms() -> int:
    return int(time.time() * 1000)


__all__ = ["OpenCodeSubagentSessionProbe"]
