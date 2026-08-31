"""Native OpenCode subagent evidence read from OpenCode's own session store.

``opencode run --format json`` (1.18.x) emits ``step_start`` when the parent
turn dispatches a native ``task`` subagent and then NOTHING until that call
completes -- the whole ``tool_use`` frame is buffered until the child is done.
A silent parent therefore carries no first-party evidence for the entire
subagent run, and the idle watchdog killed healthy runs at the 240 s
``NO_PROGRESS_QUIET`` ceiling (no OS descendants) or the 600 s
``CHILDREN_PERSIST_TOO_LONG`` ceiling (child shelling out), while native
subagents measured against an operator's store routinely ran 10-15 minutes.

OpenCode does persist every child session as it works. Its SQLite store
(``$XDG_DATA_HOME/opencode/opencode.db``, default ``~/.local/share``) carries
one ``session`` row per subagent with ``parent_id`` set to the dispatching
session, and upserts one ``part`` row per tool call, reasoning block, or
text block with an advancing ``time_updated``. Reading that store read-only
turns each newly updated part into demonstrated child work -- the same
contract the Claude subagent transcript tailer implements from
``~/.claude/projects/<key>/<session>/subagents/*.jsonl``.

The probe is deliberately observation-only and fail-quiet: a missing store,
a locked page, or an unexpected row shape yields no evidence rather than a
false liveness signal, so a genuinely wedged child still reaches the
existing ceilings.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, cast

from loguru import logger

if TYPE_CHECKING:
    from collections.abc import Callable

#: Only the newest parts are read per poll; a healthy child writes a handful
#: of parts per second at most, so this bounds one poll's work without ever
#: skipping evidence (the high-water mark advances only over returned rows).
_FETCH_LIMIT: int = 256
#: Dedupe window for ``(part_id, time_updated)`` pairs already forwarded.
_MAX_SEEN_PARTS: int = 512
#: Cache of child session ids already announced to the operator log.
_MAX_KNOWN_CHILDREN: int = 64
#: Minimum seconds between reconnect attempts after the store was unreachable.
_RECONNECT_BACKOFF_SECONDS: float = 30.0

#: Column count of :data:`_CHILD_PART_QUERY`; rows of any other width are dropped.
_CHILD_PART_COLUMNS: int = 6

_CHILD_PART_QUERY: str = (
    "SELECT p.id, p.session_id, s.agent, s.title, p.time_updated, p.data "
    "FROM part AS p JOIN session AS s ON s.id = p.session_id "
    "WHERE s.parent_id = ? AND p.time_updated >= ? "
    "ORDER BY p.time_updated ASC, p.id ASC LIMIT ?"
)


@dataclass(frozen=True)
class OpenCodeChildPart:
    """One updated message part belonging to a native OpenCode subagent."""

    child_session_id: str
    agent: str | None
    title: str
    part_id: str
    kind: str
    time_updated_ms: int


class OpenCodeChildPartSource(Protocol):
    """Where child-session parts come from; the SQLite store in production."""

    def fetch(self, parent_session_id: str, since_ms: int) -> list[OpenCodeChildPart]:
        """Return parts of children of ``parent_session_id`` updated at or after ``since_ms``."""
        ...

    def close(self) -> None:
        """Release any handle the source holds."""
        ...


def default_opencode_db_path() -> Path:
    """Return OpenCode's SQLite store path under the XDG data directory."""
    xdg_data_home = os.environ.get("XDG_DATA_HOME")
    data_root = Path(xdg_data_home) if xdg_data_home else Path.home() / ".local" / "share"
    return data_root / "opencode" / "opencode.db"


def part_kind_from_data(data: str) -> str:
    """Summarise a ``part.data`` JSON payload as ``tool:<name>`` / ``text`` / ``reasoning``."""
    try:
        decoded = cast("object", json.loads(data))
    except (TypeError, ValueError):
        return "part"
    if not isinstance(decoded, dict):
        return "part"
    payload = cast("dict[str, object]", decoded)
    part_type = payload.get("type")
    if not isinstance(part_type, str) or not part_type:
        return "part"
    if part_type == "tool":
        tool = payload.get("tool")
        if isinstance(tool, str) and tool:
            return f"tool:{tool}"
    return part_type


class SqliteOpenCodeChildPartSource:
    """Read-only reader over OpenCode's ``opencode.db``.

    The connection is opened lazily with a bounded busy timeout and a
    ``mode=ro`` URI so the probe can never write to, lock, or create the
    store. Any ``sqlite3`` error empties the result and schedules a
    reconnect after :data:`_RECONNECT_BACKOFF_SECONDS`.
    """

    def __init__(
        self,
        db_path: Path,
        *,
        timeout_seconds: float = 0.5,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._db_path = db_path
        self._timeout = timeout_seconds
        self._monotonic = monotonic
        self._conn: sqlite3.Connection | None = None
        self._retry_after: float | None = None

    def _connect(self) -> sqlite3.Connection | None:
        if self._conn is not None:
            return self._conn
        now = self._monotonic()
        if self._retry_after is not None and now < self._retry_after:
            return None
        # filesystem-read-ok: existence probe of OpenCode's own data store outside the workspace
        if not self._db_path.is_file():
            self._retry_after = now + _RECONNECT_BACKOFF_SECONDS
            return None
        try:
            self._conn = sqlite3.connect(
                f"file:{self._db_path}?mode=ro",
                uri=True,
                timeout=self._timeout,
                check_same_thread=False,
            )
        except sqlite3.Error:
            self._retry_after = now + _RECONNECT_BACKOFF_SECONDS
            return None
        return self._conn

    def fetch(self, parent_session_id: str, since_ms: int) -> list[OpenCodeChildPart]:
        conn = self._connect()
        if conn is None:
            return []
        try:
            rows = cast(
                "list[tuple[object, ...]]",
                conn.execute(_CHILD_PART_QUERY, (parent_session_id, since_ms, _FETCH_LIMIT)).fetchall(),
            )
        except sqlite3.Error:
            self.close()
            self._retry_after = self._monotonic() + _RECONNECT_BACKOFF_SECONDS
            return []
        parts: list[OpenCodeChildPart] = []
        for row in rows:
            part = _row_to_part(row)
            if part is not None:
                parts.append(part)
        return parts

    def close(self) -> None:
        conn = self._conn
        self._conn = None
        if conn is not None:
            try:
                conn.close()
            except sqlite3.Error:
                return


def _row_to_part(row: tuple[object, ...]) -> OpenCodeChildPart | None:
    if len(row) != _CHILD_PART_COLUMNS:
        return None
    part_id, session_id, agent, title, time_updated, data = row
    if not isinstance(part_id, str) or not isinstance(session_id, str):
        return None
    if isinstance(time_updated, bool) or not isinstance(time_updated, int):
        return None
    return OpenCodeChildPart(
        child_session_id=session_id,
        agent=agent if isinstance(agent, str) and agent else None,
        title=title if isinstance(title, str) else "",
        part_id=part_id,
        kind=part_kind_from_data(data) if isinstance(data, str) else "part",
        time_updated_ms=time_updated,
    )


def summarize_child_part(part: OpenCodeChildPart) -> str:
    """Render a part as the ``verb:<name> [child:<agent>] <title>`` watchdog summary.

    The ``tool_use:`` / ``text:`` / ``thinking:`` prefixes are the canonical
    subagent-description vocabulary the watchdog surfaces as the current
    tool call; the ``[child:<agent>]`` label keeps the line attributed to
    the subagent rather than the parent.
    """
    label = f"[child:{part.agent}]" if part.agent else "[child]"
    if part.kind.startswith("tool:"):
        verb = f"tool_use:{part.kind[len('tool:'):]}"
    elif part.kind == "reasoning":
        verb = "thinking:"
    elif part.kind == "text":
        verb = "text:"
    else:
        verb = f"{part.kind}:"
    return f"{verb} {label} {part.title}".rstrip()


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
        self._seen: OrderedDict[tuple[str, int], None] = OrderedDict()  # bounded-accumulator-ok: capped at _MAX_SEEN_PARTS
        self._known_children: OrderedDict[str, None] = OrderedDict()  # bounded-accumulator-ok: capped at _MAX_KNOWN_CHILDREN
        self._closed = False

    @property
    def observed_children(self) -> frozenset[str]:
        """Child session ids that have produced at least one part."""
        return frozenset(self._known_children)

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


__all__ = [
    "OpenCodeChildPart",
    "OpenCodeChildPartSource",
    "OpenCodeSubagentSessionProbe",
    "SqliteOpenCodeChildPartSource",
    "default_opencode_db_path",
    "part_kind_from_data",
    "summarize_child_part",
]
