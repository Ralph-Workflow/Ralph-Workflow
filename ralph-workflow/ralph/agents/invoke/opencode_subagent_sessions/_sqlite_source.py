"""Read-only reader over OpenCode's ``opencode.db``."""

from __future__ import annotations

import sqlite3
import time
from typing import TYPE_CHECKING, cast
from urllib.parse import quote

from ._child_part import OpenCodeChildPart, part_kind_from_data

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

#: Only the newest parts are read per poll; a healthy child writes a handful
#: of parts per second at most, so this bounds one poll's work without ever
#: skipping evidence (the high-water mark advances only over returned rows).
_FETCH_LIMIT: int = 256
#: Minimum seconds between reconnect attempts after the store was unreachable.
_RECONNECT_BACKOFF_SECONDS: float = 30.0

#: Column count of :data:`_CHILD_PART_QUERY`; rows of any other width are dropped.
_CHILD_PART_COLUMNS: int = 6

#: Subagents may themselves dispatch ``task`` when an agent config grants the
#: permission (1.18.x denies it by default); walk the whole lineage so a
#: grandchild's work is not hidden behind an intermediate child idling on its
#: own ``task`` call. The depth cap bounds the walk on a corrupt cyclic tree.
_MAX_LINEAGE_DEPTH: int = 8

_CHILD_PART_QUERY: str = (
    "WITH RECURSIVE lineage(id, depth) AS ("
    " SELECT id, 1 FROM session WHERE parent_id = ?"
    " UNION ALL"
    " SELECT s.id, l.depth + 1 FROM session AS s JOIN lineage AS l ON s.parent_id = l.id"
    " WHERE l.depth < ?"
    ") "
    "SELECT p.id, p.session_id, s.agent, s.title, p.time_updated, p.data "
    "FROM part AS p JOIN session AS s ON s.id = p.session_id "
    "WHERE p.session_id IN (SELECT id FROM lineage) AND p.time_updated >= ? "
    "ORDER BY p.time_updated ASC, p.id ASC LIMIT ?"
)


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
                f"file:{quote(str(self._db_path))}?mode=ro",
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
                conn.execute(
                    _CHILD_PART_QUERY,
                    (parent_session_id, _MAX_LINEAGE_DEPTH, since_ms, _FETCH_LIMIT),
                ).fetchall(),
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


__all__ = ["SqliteOpenCodeChildPartSource"]
