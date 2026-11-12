from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import AbstractContextManager
from typing import cast

from ralph.mcp.explore._store_types import (
    FileRow,
    _row_int_opt,
    _row_to_file,
)


class _FileRowMethods:
    _conn: sqlite3.Connection
    _transaction: Callable[[], AbstractContextManager[sqlite3.Cursor]]

    def upsert_file(self, row: FileRow) -> None:
        with self._transaction() as cur:
            cur.execute(
                """
                INSERT INTO files (
                    path, content_hash, size_bytes, mtime_ns, language,
                    indexed_generation, indexed_at, is_deleted
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(path) DO UPDATE SET
                    content_hash=excluded.content_hash,
                    size_bytes=excluded.size_bytes,
                    mtime_ns=excluded.mtime_ns,
                    language=excluded.language,
                    indexed_generation=excluded.indexed_generation,
                    indexed_at=excluded.indexed_at,
                    is_deleted=excluded.is_deleted
                """,
                (
                    row.path,
                    row.content_hash,
                    row.size_bytes,
                    row.mtime_ns,
                    row.language,
                    row.indexed_generation,
                    row.indexed_at,
                    1 if row.is_deleted else 0,
                ),
            )

    def get_file(self, path: str) -> FileRow | None:
        cur = self._conn.execute("SELECT * FROM files WHERE path = ?", (path,))
        row: sqlite3.Row | None = cur.fetchone()
        if row is None:
            return None
        return _row_to_file(row)

    def get_file_many(self, paths: Sequence[str]) -> dict[str, FileRow]:
        if not paths:
            return {}
        placeholders = ",".join("?" for _ in paths)
        cur = self._conn.execute(
            f"SELECT * FROM files WHERE path IN ({placeholders})",
            tuple(paths),
        )
        all_rows = cast("list[sqlite3.Row]", cur.fetchall())
        result: dict[str, FileRow] = {}
        for row in all_rows:
            file_row = _row_to_file(row)
            result[file_row.path] = file_row
        return result

    def bulk_size_mtime_for_paths(self, paths: Sequence[str]) -> dict[str, tuple[int, int]]:
        if not paths:
            return {}
        placeholders = ",".join("?" for _ in paths)
        cur = self._conn.execute(
            f"SELECT path, size_bytes, mtime_ns FROM files "
            f"WHERE is_deleted = 0 AND path IN ({placeholders})",
            tuple(paths),
        )
        all_rows = cast("list[sqlite3.Row]", cur.fetchall())
        result: dict[str, tuple[int, int]] = {}
        for row in all_rows:
            typed_row = cast("Mapping[str, int | str]", row)
            try:
                size_obj = typed_row["size_bytes"]
                mtime_obj = typed_row["mtime_ns"]
                path_value = typed_row["path"]
            except (KeyError, IndexError):
                continue
            if not isinstance(path_value, str):
                continue
            size = (
                int(size_obj) if isinstance(size_obj, int) and not isinstance(size_obj, bool) else 0
            )
            mtime = (
                int(mtime_obj)
                if isinstance(mtime_obj, int) and not isinstance(mtime_obj, bool)
                else 0
            )
            result[path_value] = (size, mtime)
        return result

    def upsert_file_many(self, rows: Sequence[FileRow]) -> None:
        if not rows:
            return
        params = [
            (
                row.path,
                row.content_hash,
                row.size_bytes,
                row.mtime_ns,
                row.language,
                row.indexed_generation,
                row.indexed_at,
                1 if row.is_deleted else 0,
            )
            for row in rows
        ]
        with self._transaction() as cur:
            cur.executemany(
                """
                INSERT INTO files (
                    path, content_hash, size_bytes, mtime_ns, language,
                    indexed_generation, indexed_at, is_deleted
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(path) DO UPDATE SET
                    content_hash=excluded.content_hash,
                    size_bytes=excluded.size_bytes,
                    mtime_ns=excluded.mtime_ns,
                    language=excluded.language,
                    indexed_generation=excluded.indexed_generation,
                    indexed_at=excluded.indexed_at,
                    is_deleted=excluded.is_deleted
                """,
                params,
            )

    def upsert_manifest_many(
        self,
        paths: Sequence[str],
        content_hashes: Sequence[str],
        sizes: Sequence[int],
        mtimes: Sequence[int],
        last_seen_generation: int,
    ) -> None:
        if not paths:
            return
        if not (len(paths) == len(content_hashes) == len(sizes) == len(mtimes)):
            raise ValueError("upsert_manifest_many: all sequence arguments must be the same length")
        params = [
            (
                paths[i],
                content_hashes[i],
                sizes[i],
                mtimes[i],
                None,
                last_seen_generation,
            )
            for i in range(len(paths))
        ]
        with self._transaction() as cur:
            cur.executemany(
                """
                INSERT INTO manifest (
                    path, content_hash, size_bytes, mtime_ns,
                    inode_or_file_id, last_seen_generation
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(path) DO UPDATE SET
                    content_hash=excluded.content_hash,
                    size_bytes=excluded.size_bytes,
                    mtime_ns=excluded.mtime_ns,
                    last_seen_generation=excluded.last_seen_generation
                """,
                params,
            )

    def iter_files(self) -> Iterator[FileRow]:
        cur = self._conn.execute("SELECT * FROM files WHERE is_deleted = 0")
        all_rows = cast("list[sqlite3.Row]", cur.fetchall())
        for row in all_rows:
            yield _row_to_file(row)

    def count_files(self) -> int:
        cur = self._conn.execute("SELECT COUNT(*) FROM files WHERE is_deleted = 0")
        row: sqlite3.Row | None = cur.fetchone()
        return _row_int_opt(row, 0) if row is not None else 0

    def count_deleted_files(self) -> int:
        cur = self._conn.execute("SELECT COUNT(*) FROM files WHERE is_deleted = 1")
        row: sqlite3.Row | None = cur.fetchone()
        return _row_int_opt(row, 0) if row is not None else 0

    def has_deleted_files(self) -> bool:
        cur = self._conn.execute("SELECT EXISTS(SELECT 1 FROM files WHERE is_deleted = 1)")
        row: sqlite3.Row | None = cur.fetchone()
        if row is None:
            return False
        return _row_int_opt(row, 0) > 0

    def delete_file_rows(self, path: str) -> None:
        with self._transaction() as cur:
            cur.execute(
                """
                DELETE FROM chunks_fts WHERE rowid IN (
                    SELECT fts_rowid FROM chunks WHERE path = ? AND fts_rowid IS NOT NULL
                )
                """,
                (path,),
            )
            cur.execute("DELETE FROM files WHERE path = ?", (path,))
            cur.execute("DELETE FROM chunks WHERE path = ?", (path,))
            cur.execute("DELETE FROM evidence WHERE path = ?", (path,))
