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
    _conn: sqlite3.Connection
    _transaction: Callable[[], AbstractContextManager[sqlite3.Cursor]]

    def lookup_content_cache(
        self,
        *,
        content_hash: str,
        extractor_version: str,
    ) -> ContentCacheRow | None:
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

    def insert_content_cache(
        self,
        *,
        row: ContentCacheRow,
    ) -> None:
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

    def delete_content_cache(self, *, content_hash: str) -> None:
        with self._transaction() as cur:
            cur.execute(
                "DELETE FROM content_cache WHERE content_hash = ?",
                (content_hash,),
            )

    def content_cache_size(self) -> int:
        cur = self._conn.execute("SELECT COUNT(*) FROM content_cache")
        row: sqlite3.Row | None = cur.fetchone()
        return _row_int_opt(row, 0) if row is not None else 0

    def iter_content_cache(self) -> Iterator[ContentCacheRow]:
        cur = self._conn.execute("SELECT * FROM content_cache")
        rows = cast("list[sqlite3.Row]", cur.fetchall())
        for cache_row in rows:
            yield _row_to_content_cache(cache_row)


__all__ = ["_ContentCacheMethods"]
