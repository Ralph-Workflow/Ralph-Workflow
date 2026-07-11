"""Content-cache method mixin for :class:`ExploreStore`.

Extracted from :mod:`ralph.mcp.explore._store_class` so the
:class:`ExploreStore` implementation file stays under the
per-file line ceiling.

The methods live on a mixin class so the SQLite + cache logic
remains cohesive (the cache owns dedicated ``content_cache``
and ``content_cache_payload`` tables) while the main store
class only imports a small ``_ContentCacheMethods`` alias.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager
from typing import cast

from ralph.mcp.explore._store_types import (
    ContentCacheRow,
    _row_int_opt,
    _row_to_content_cache,
)


class _ContentCacheMethods:
    """Mixin providing content-cache accessors for ``ExploreStore``.

    The mixin assumes the host class exposes two private members:

    * ``_conn`` — the SQLite connection (shared across all
      methods on the same instance).
    * ``_transaction()`` — a context manager yielding a cursor
      bound to a short transaction.

    These are part of the implementation contract between the
    mixin and :class:`ExploreStore`; the runtime enforces them
    through Python's duck typing while the type checker reads
    them as ``Any`` via the explicit annotation below.
    """

    # Ponytail: declare the private members the mixin depends on
    # as untyped attributes so mypy does not flag the cross-class
    # access. The runtime contract is documented in the class
    # docstring; a regression that breaks the contract would
    # raise ``AttributeError`` at the first call.
    _conn: sqlite3.Connection
    _transaction: Callable[[], AbstractContextManager[sqlite3.Cursor]]

    # --- Content cache (AC-05: deterministic extraction reuse) -------

    def lookup_content_cache(
        self,
        *,
        content_hash: str,
        extractor_version: str,
    ) -> ContentCacheRow | None:
        """Return the cached payload for ``content_hash`` when fresh.

        AC-05/AC-06: a cache hit only counts when ``extractor_version``
        matches the in-process constant; an older or newer version
        forces a re-extract so the cache can never serve stale
        payloads to callers. This is the only signal that drives
        the reindex pipeline's copy/move reuse path.
        """
        cur = self._conn.execute(
            """
            SELECT * FROM content_cache
            WHERE content_hash = ? AND extractor_version = ?
            """,
            (content_hash, extractor_version),
        )
        row: sqlite3.Row | None = cur.fetchone()
        if row is None:
            return None
        return _row_to_content_cache(row)

    def read_content_cache_payload(
        self,
        *,
        content_hash: str,
    ) -> bytes | None:
        """Return the cached payload BLOB for ``content_hash`` or ``None``.

        The BLOB is opaque to the store; callers pass it to
        :func:`deserialize_content_cache_payload` to recover the
        typed cache record (chunks/FTS payload structure). A
        missing payload alongside an existing metadata row is
        still returned as ``None`` so the caller can decide
        whether to repopulate.
        """
        cur = self._conn.execute(
            "SELECT payload FROM content_cache_payload WHERE content_hash = ?",
            (content_hash,),
        )
        row: sqlite3.Row | None = cur.fetchone()
        if row is None:
            return None
        value = cast("bytes | memoryview | None", row["payload"])
        if value is None:
            return None
        if isinstance(value, bytes):
            return value
        return bytes(value)

    def insert_content_cache(
        self,
        *,
        row: ContentCacheRow,
        payload: bytes,
    ) -> None:
        """Insert or refresh the cache metadata + payload for ``content_hash``.

        Idempotent: a second insert with the same ``content_hash``
        refreshes ``extracted_at`` and ``extractor_version`` while
        retaining the prior ``extraction_status``. The payload
        table uses ``ON CONFLICT(content_hash) DO UPDATE`` so the
        BLOB stays in sync with the metadata.
        """
        with self._transaction() as cur:
            cur.execute(
                """
                INSERT INTO content_cache (
                    content_hash, language, extractor_version,
                    extracted_at, extraction_status, error_summary
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(content_hash) DO UPDATE SET
                    language=excluded.language,
                    extractor_version=excluded.extractor_version,
                    extracted_at=excluded.extracted_at,
                    extraction_status=excluded.extraction_status,
                    error_summary=excluded.error_summary
                """,
                (
                    row.content_hash,
                    row.language,
                    row.extractor_version,
                    row.extracted_at,
                    row.extraction_status,
                    row.error_summary,
                ),
            )
            cur.execute(
                """
                INSERT INTO content_cache_payload (content_hash, payload)
                VALUES (?, ?)
                ON CONFLICT(content_hash) DO UPDATE SET payload=excluded.payload
                """,
                (row.content_hash, payload),
            )

    def delete_content_cache(self, *, content_hash: str) -> None:
        """Remove a single cache entry (metadata + payload) by hash."""
        with self._transaction() as cur:
            cur.execute(
                "DELETE FROM content_cache WHERE content_hash = ?",
                (content_hash,),
            )
            cur.execute(
                "DELETE FROM content_cache_payload WHERE content_hash = ?",
                (content_hash,),
            )

    def content_cache_size(self) -> int:
        """Return the current cache row count (bounded O(1) aggregate)."""
        cur = self._conn.execute("SELECT COUNT(*) FROM content_cache")
        row: sqlite3.Row | None = cur.fetchone()
        return _row_int_opt(row, 0) if row is not None else 0

    def iter_content_cache(self) -> Iterator[ContentCacheRow]:
        """Yield every ``ContentCacheRow`` for inspection/audit use only."""
        cur = self._conn.execute("SELECT * FROM content_cache")
        rows = cast("list[sqlite3.Row]", cur.fetchall())
        for cache_row in rows:
            yield _row_to_content_cache(cache_row)


__all__ = ["_ContentCacheMethods"]
