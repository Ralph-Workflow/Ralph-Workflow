"""File-row and manifest access mixin for :class:`ExploreStore`.

Extracted from :mod:`ralph.mcp.explore._store_class` so the hub
module stays under the repository file-size limit. The methods
cover single-file upsert / read, bulk reads / writes for the warm
no-op path, manifest bulk upsert, and the bounded file-row
introspection queries (count / exists / delete) used by
``git_status`` and the staleness probe.

The mixin depends on the host class providing ``self._conn`` (a
:class:`sqlite3.Connection`) and ``self._transaction`` (a
context-manager factory).
"""

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
    """Mixin supplying the file-row and manifest access methods."""

    # Ponytail: declare the private members the mixin depends on as
    # untyped class-level annotations so mypy does not flag the
    # cross-class access. The runtime contract is documented in the
    # class docstring; a regression that breaks the contract would
    # raise ``AttributeError`` at the first call.
    _conn: sqlite3.Connection
    _transaction: Callable[[], AbstractContextManager[sqlite3.Cursor]]

    def upsert_file(self, row: FileRow) -> None:
        """Insert or replace a file row."""
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
        """Bulk variant of :meth:`get_file`.

        Returns ``{path: FileRow}`` for every persisted ``files``
        entry whose ``path`` is in ``paths``. Missing paths are
        simply absent from the result. The whole lookup is one
        ``SELECT`` with an ``IN (?, ?, ...)`` predicate so the warm
        no-op reindex can fetch the previous (size, mtime) for a
        whole batch without issuing one statement per path.

        An empty ``paths`` input performs no SQL and returns
        ``{}``; callers can pass a full batch without a guard.
        """
        if not paths:
            return {}
        placeholders = ",".join("?" for _ in paths)
        cur = self._conn.execute(
            f"SELECT * FROM files WHERE path IN ({placeholders})",
            tuple(paths),
        )
        all_rows = cast(
            "list[sqlite3.Row]", cur.fetchall()
        )  # cast-policy: seam: structural boundary (sqlite Row / lazy module attr / protocol conferee)
        result: dict[str, FileRow] = {}
        for row in all_rows:
            file_row = _row_to_file(row)
            result[file_row.path] = file_row
        return result

    def bulk_size_mtime_for_paths(self, paths: Sequence[str]) -> dict[str, tuple[int, int]]:
        """Bulk-load ``(size_bytes, mtime_ns)`` for ``paths``.

        Returns ``{path: (size_bytes, mtime_ns)}`` for every
        persisted ``files`` row whose ``path`` is in ``paths`` and
        ``is_deleted = 0``. Missing or unknown paths are simply
        absent from the result; the caller treats them as drift.

        The query is one ``SELECT`` with an ``IN (?, ?, ...)``
        predicate so the F12 staleness probe can compare a whole
        on-disk ``(size, mtime)`` manifest against the persisted
        rows in a single round-trip rather than issuing one
        ``SELECT`` per path. The projection drops every column
        beyond ``path / size_bytes / mtime_ns`` so SQLite does not
        pay the row-decode cost for content-hash / language /
        generation columns the staleness probe never reads.

        Empty ``paths`` performs no SQL and returns ``{}``;
        callers can pass the full workspace manifest without a
        guard.
        """
        if not paths:
            return {}
        placeholders = ",".join("?" for _ in paths)
        cur = self._conn.execute(
            f"SELECT path, size_bytes, mtime_ns FROM files "
            f"WHERE is_deleted = 0 AND path IN ({placeholders})",
            tuple(paths),
        )
        all_rows = cast(
            "list[sqlite3.Row]", cur.fetchall()
        )  # cast-policy: seam: structural boundary (sqlite Row / lazy module attr / protocol conferee)
        result: dict[str, tuple[int, int]] = {}
        for row in all_rows:
            # sqlite3.Row.__getitem__ is typed as ``Any`` by mypy because the
            # Row object doesn't carry a column type. The SELECT above is a
            # fixed 3-column projection (``path`` is text, ``size_bytes`` and
            # ``mtime_ns`` are integers), so we cast the row to a typed mapping
            # view to keep the read path statically typed.
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
        """Bulk variant of :meth:`upsert_file`.

        Persists every row in ``rows`` via a single
        ``executemany`` so the warm no-op reindex can refresh the
        ``files`` table for an entire batch without one statement
        per path. Empty input is a no-op (no transaction, no SQL).
        """
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
        """Bulk upsert of ``manifest`` rows for the warm no-op path.

        Every argument must be the same length; ``paths[i]`` is
        written with ``content_hashes[i]``, ``sizes[i]``,
        ``mtimes[i]``. The whole upsert is one ``executemany`` so
        the reindex pipeline can refresh the ``last_seen_generation``
        for the entire unchanged workspace in one statement. Empty
        input is a no-op (no transaction, no SQL).
        """
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
        all_rows = cast(
            "list[sqlite3.Row]", cur.fetchall()
        )  # cast-policy: seam: structural boundary (sqlite Row / lazy module attr / protocol conferee)
        for row in all_rows:
            yield _row_to_file(row)

    def count_files(self) -> int:
        """Return the live file row count.

        Bounded: a single ``COUNT(*)`` aggregate, no row
        materialization. Callers that need per-row data use
        :meth:`iter_files`. The method exists so the index
        status and git_status compact paths can compute
        freshness with O(1) work instead of pulling the entire
        ``files`` table into memory.
        """
        cur = self._conn.execute("SELECT COUNT(*) FROM files WHERE is_deleted = 0")
        row: sqlite3.Row | None = cur.fetchone()
        return _row_int_opt(row, 0) if row is not None else 0

    def count_deleted_files(self) -> int:
        """Return the count of file rows marked ``is_deleted=1``.

        Bounded: a single ``COUNT(*)`` aggregate. ``iter_files``
        filters out deleted rows, so this method is the only
        bounded way for callers to observe the deleted-row
        stale signal without materializing the entire table.
        """
        cur = self._conn.execute("SELECT COUNT(*) FROM files WHERE is_deleted = 1")
        row: sqlite3.Row | None = cur.fetchone()
        return _row_int_opt(row, 0) if row is not None else 0

    def has_deleted_files(self) -> bool:
        """Bounded existence check for any deleted file row.

        Equivalent to ``count_deleted_files() > 0`` but uses
        ``EXISTS`` so SQLite short-circuits on the first match.
        Callers that only need a boolean freshness signal
        (e.g., compact ``git_status``) should prefer this
        method over a count query.
        """
        cur = self._conn.execute("SELECT EXISTS(SELECT 1 FROM files WHERE is_deleted = 1)")
        row: sqlite3.Row | None = cur.fetchone()
        if row is None:
            return False
        return _row_int_opt(row, 0) > 0

    def delete_file_rows(self, path: str) -> None:
        """Remove file/chunk/evidence rows for ``path`` in current generation."""
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
