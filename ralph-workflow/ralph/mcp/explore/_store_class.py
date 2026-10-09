"""ExploreStore class implementation for the indexed exploration substrate.

Extracted from :mod:`ralph.mcp.explore.store` so the hub module
stays under the per-file line ceiling. The class depends on the
dataclasses and helpers in :mod:`ralph.mcp.explore._store_types`;
the hub :mod:`ralph.mcp.explore.store` re-exports this class for
backward compatibility with existing callers.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import cast

from ralph.mcp.explore._store_class_content_cache import _ContentCacheMethods
from ralph.mcp.explore._store_class_file_rows import _FileRowMethods
from ralph.mcp.explore._store_class_init import _InitializeMethods
from ralph.mcp.explore._store_class_retention import _RetentionMethods
from ralph.mcp.explore._store_types import (
    DEFAULT_BUSY_TIMEOUT_MS,
    DEFAULT_INDEX_DB,
    ChunkRow,
    EdgeRow,
    EvidenceRow,
    SpanRow,
    SymbolRow,
    _row_int_opt,
    _row_str,
    _row_to_edge,
    _row_to_evidence,
    _row_to_span,
    _row_to_symbol,
    normalize_index_path,
    real_clock_seconds,
)

logger = logging.getLogger(__name__)


class ExploreStore(
    _ContentCacheMethods,
    _FileRowMethods,
    _InitializeMethods,
    _RetentionMethods,
):
    """Owns the SQLite connection and DDL for the index.

    Construct with an explicit index directory. WAL mode + busy
    timeout are configured at construction time so every subsequent
    call inherits the bounded-subprocess contract.
    """

    def __init__(
        self,
        index_dir: Path,
        *,
        busy_timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS,
    ) -> None:
        self.index_dir = Path(index_dir)
        # filesystem-write-ok: engine-internal SQLite directory creation is required before durable index publication.
        self.index_dir.mkdir(parents=True, exist_ok=True)
        self._db_path = self.index_dir / DEFAULT_INDEX_DB
        self._busy_timeout_ms = busy_timeout_ms
        self._db_inode: int | None = None
        self._in_transaction: bool = False
        try:
            self._db_inode = self._db_path.stat().st_ino
        except OSError:
            self._db_inode = None
        # AC-02/AC-05: open with ``check_same_thread=False`` so
        # concurrent reindex claims (public ``ralph_reindex`` and
        # lifecycle hooks) can each hold their own connection
        # without blocking the cross-thread single-writer seam.
        # Without this, a second thread touching the same
        # ``ExploreStore`` instance raises
        # ``ProgrammingError: SQLite objects created in a thread
        # can only be used in that same thread`` and the call
        # hangs forever on the GIL-released busy_timeout wait.
        # WAL mode + the ``ReindexWriter.claim`` lock serialize
        # writers, so cross-thread access is safe.
        self._conn = sqlite3.connect(
            str(self._db_path),
            timeout=busy_timeout_ms / 1000.0,
            isolation_level=None,
            check_same_thread=False,
        )
        self._conn.row_factory = sqlite3.Row
        self._initialize()

    # ``_initialize`` and the S-4 concurrency helpers
    # (``_schema_version_matches``, ``_initialize_with_bounded_retry``,
    # ``_INIT_LOCK_ATTEMPTS``, ``_INIT_LOCK_BACKOFF_SECONDS``) live
    # in :mod:`ralph.mcp.explore._store_class_init` and are mixed
    # in via ``_InitializeMethods``.

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Cursor]:
        """Run a transaction with explicit BEGIN/COMMIT (reentrant)."""
        if self._in_transaction:
            cur = self._conn.cursor()
            try:
                yield cur
            finally:
                cur.close()
            return

        self._in_transaction = True
        cur = self._conn.cursor()
        try:
            cur.execute("BEGIN IMMEDIATE")
            yield cur
            cur.execute("COMMIT")
        except BaseException:
            with suppress(sqlite3.OperationalError):
                cur.execute("ROLLBACK")
            raise
        finally:
            self._in_transaction = False
            cur.close()

    transaction = _transaction

    # --- Lifecycle ---------------------------------------------------

    @property
    def db_path(self) -> Path:
        return self._db_path

    def close(self) -> None:
        self._conn.close()

    def reopen(self) -> None:
        """Close the live connection and reopen it against the same file.

        Used by the staged ``mode='full'`` reindex after the
        staging database is swapped in at the file level: the
        connection must be re-opened to observe the new file
        content. Pragmas are reapplied because the prior
        connection is gone; the DDL is left to ``_initialize``
        and is idempotent (the staging file already contains
        the DDL).
        """
        with suppress(sqlite3.ProgrammingError):
            # Already closed; safe to ignore.
            self._conn.close()
        self._conn = sqlite3.connect(
            str(self._db_path),
            timeout=self._busy_timeout_ms / 1000.0,
            isolation_level=None,
            check_same_thread=False,
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(f"PRAGMA busy_timeout={self._busy_timeout_ms}")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        # S-3 (wt-11): record the current on-disk inode so the
        # next ``maybe_reopen_after_swap`` call can detect a
        # cross-process atomic-rename swap. The inode is the
        # cheapest stable identity: the OLD inode is preserved
        # while any fd holds it, but the on-disk path resolves
        # to the NEW inode after the swap.
        try:
            self._db_inode = self._db_path.stat().st_ino
        except OSError:
            self._db_inode = None

    def maybe_reopen_after_swap(self) -> bool:
        """Reopen the connection if the on-disk database was swapped.

        S-3 (wt-11): the live connection points to the inode
        that was current when ``__init__`` ran. A cross-process
        atomic-rename swap (the staged full rebuild path)
        unlinks the OLD inode from the directory but keeps it
        alive via the open fd, so the OLD inode still serves
        the OLD generation. ``stat()`` on the db path
        resolves to the NEW inode, so a single ``stat`` lets
        the next caller reopen against the fresh data.

        Returns True iff a reopen actually fired. The check
        is bounded and fail-open: any OSError returns False
        and leaves the existing connection in place so a
        transient stat failure cannot poison the handle.
        """
        try:
            current_inode = self._db_path.stat().st_ino
        except OSError:
            return False
        if current_inode == self._db_inode:
            return False
        self.reopen()
        return True

    # --- Chunk + FTS5 -------------------------------------------------

    def upsert_chunk(self, chunk: ChunkRow, text: str) -> None:
        """Insert or replace a chunk and its FTS5 row."""
        with self._transaction() as cur:
            cur.execute(
                """
                INSERT INTO chunks (
                    chunk_id, path, start_line, end_line, text_hash,
                    role, generation
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(chunk_id) DO UPDATE SET
                    path=excluded.path,
                    start_line=excluded.start_line,
                    end_line=excluded.end_line,
                    text_hash=excluded.text_hash,
                    role=excluded.role,
                    generation=excluded.generation
                """,
                (
                    chunk.chunk_id,
                    chunk.path,
                    chunk.start_line,
                    chunk.end_line,
                    chunk.text_hash,
                    chunk.role,
                    chunk.generation,
                ),
            )
            # FTS5 external-content table: delete the prior FTS row by
            # its stored rowid (O(log n)); the UNINDEXED columns cannot
            # serve a WHERE chunk_id lookup without a full FTS scan.
            cur.execute(
                """DELETE FROM chunks_fts WHERE rowid = (
                    SELECT fts_rowid FROM chunks
                    WHERE chunk_id = ? AND fts_rowid IS NOT NULL
                )""",
                (chunk.chunk_id,),
            )
            cur.execute(
                """
                INSERT INTO chunks_fts (
                    text, path, chunk_id, symbol_names,
                    headings, comments
                ) VALUES (?, ?, ?, '', '', '')
                """,
                (text, chunk.path, chunk.chunk_id),
            )
            fts_rowid = int(cur.lastrowid) if cur.lastrowid is not None else None
            cur.execute(
                "UPDATE chunks SET fts_rowid = ? WHERE chunk_id = ?",
                (fts_rowid, chunk.chunk_id),
            )

    def delete_chunks_for_path(self, path: str) -> None:
        """Delete all chunks and FTS rows for ``path``."""
        with self._transaction() as cur:
            cur.execute(
                """
                DELETE FROM chunks_fts WHERE rowid IN (
                    SELECT fts_rowid FROM chunks WHERE path = ? AND fts_rowid IS NOT NULL
                )
                """,
                (path,),
            )
            cur.execute("DELETE FROM chunks WHERE path = ?", (path,))

    def fts_search(
        self,
        query: str,
        *,
        limit: int = 100,
        path_prefix: str | None = None,
        include_globs: Sequence[str] | None = None,
        exclude_globs: Sequence[str] | None = None,
    ) -> list[sqlite3.Row]:
        """Run an FTS5 MATCH query and return rows.

        Returns ``chunks_fts`` rows (path, chunk_id, text). Callers
        are expected to translate chunk_id to evidence handles.

        AC-02 indexed-grep filter parity: ``path_prefix`` restricts
        matches to paths that equal the prefix or start with
        ``prefix + '/'``. ``include_globs`` / ``exclude_globs`` apply
        the same glob semantics as the legacy live grep so the
        indexed branch cannot leak out-of-scope matches.
        """
        from ralph.mcp.explore.path_filter import (
            compile_path_filter,
        )

        path_filter = compile_path_filter(
            path_prefix=path_prefix,
            include_globs=include_globs,
            exclude_globs=exclude_globs,
        )
        cur = self._conn.execute(
            """
            SELECT path, chunk_id, snippet(chunks_fts, 0, '', '', '...', 8) AS snippet
            FROM chunks_fts
            WHERE chunks_fts MATCH ?
            ORDER BY bm25(chunks_fts)
            LIMIT ?
            """,
            (query, limit),
        )
        raw_results = cast(
            "list[sqlite3.Row]", cur.fetchall()
        )  # cast-policy: seam: structural boundary (sqlite Row / lazy module attr / protocol conferee)
        if path_filter is None:
            return raw_results
        filtered: list[sqlite3.Row] = []
        for row in raw_results:
            path_obj: object = row["path"]
            path_str = str(path_obj) if path_obj is not None else ""
            if path_filter(path_str):
                filtered.append(row)
        return filtered

    def fts_search_grep(
        self,
        query: str,
        *,
        limit: int = 100,
        path_prefix: str | None = None,
        include_globs: Sequence[str] | None = None,
        exclude_globs: Sequence[str] | None = None,
    ) -> list[sqlite3.Row]:
        """Run an FTS5 MATCH and return rows pre-joined for grep post-processing.

        Returns one row per FTS5 hit with the full chunk ``text``,
        the chunk's ``start_line``/``end_line``/``text_hash``/
        ``generation``, and the parent file's ``content_hash``. The
        single JOIN removes the per-candidate ``chunks_fts.text``
        and ``chunks.start_line`` lookups the grep handler used to
        do inside the candidate loop -- a 3-queries-per-candidate
        cost that dominated indexed-grep latency on large
        ``chunks_fts`` corpora (R6.4 indexed-vs-live speed ratio).

        AC-02 indexed-grep filter parity: ``path_prefix``,
        ``include_globs``, ``exclude_globs`` apply the same
        semantics as :meth:`fts_search`. The path filter is
        evaluated against the FTS row's ``path`` so the index can
        never return out-of-scope matches.
        """
        from ralph.mcp.explore.path_filter import (
            compile_path_filter,
        )

        path_filter = compile_path_filter(
            path_prefix=path_prefix,
            include_globs=include_globs,
            exclude_globs=exclude_globs,
        )
        cur = self._conn.execute(
            """
            SELECT
                fts.path AS path,
                fts.chunk_id AS chunk_id,
                fts.text AS text,
                c.start_line AS start_line,
                c.end_line AS end_line,
                c.text_hash AS text_hash,
                c.generation AS generation,
                f.content_hash AS file_content_hash
            FROM chunks_fts AS fts
            JOIN chunks AS c ON c.chunk_id = fts.chunk_id
            JOIN files AS f ON f.path = fts.path
            WHERE fts.chunks_fts MATCH ?
              AND f.is_deleted = 0
            ORDER BY bm25(chunks_fts)
            LIMIT ?
            """,
            (query, limit),
        )
        raw_results = cast(
            "list[sqlite3.Row]", cur.fetchall()
        )  # cast-policy: seam: structural boundary (sqlite Row / lazy module attr / protocol conferee)
        if path_filter is None:
            return raw_results
        filtered: list[sqlite3.Row] = []
        for row in raw_results:
            path_obj: object = row["path"]
            path_str = str(path_obj) if path_obj is not None else ""
            if path_filter(path_str):
                filtered.append(row)
        return filtered

    # --- Evidence -----------------------------------------------------

    def insert_evidence(self, row: EvidenceRow) -> None:
        with self._transaction() as cur:
            cur.execute(
                """
                INSERT INTO evidence (
                    evidence_id, path, start_line, end_line, content_hash,
                    generation, source_tool, evidence_kind, created_at, is_stale,
                    chunk_id, span_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(evidence_id) DO UPDATE SET
                    path=excluded.path,
                    start_line=excluded.start_line,
                    end_line=excluded.end_line,
                    content_hash=excluded.content_hash,
                    generation=excluded.generation,
                    source_tool=excluded.source_tool,
                    evidence_kind=excluded.evidence_kind,
                    created_at=excluded.created_at,
                    is_stale=excluded.is_stale,
                    chunk_id=excluded.chunk_id,
                    span_id=excluded.span_id
                """,
                (
                    row.evidence_id,
                    row.path,
                    row.start_line,
                    row.end_line,
                    row.content_hash,
                    row.generation,
                    row.source_tool,
                    row.evidence_kind,
                    row.created_at,
                    1 if row.is_stale else 0,
                    row.chunk_id,
                    row.span_id,
                ),
            )

    def insert_evidence_batch(self, rows: Sequence[EvidenceRow]) -> None:
        """Insert or refresh many evidence rows in a single transaction.

        The grep handler emits one evidence row per indexed match
        (R1/R2 per-line evidence span). Calling
        :meth:`insert_evidence` per row paid an N-round-trip cost
        on large result sets -- a fresh ``BEGIN IMMEDIATE`` and
        ``COMMIT`` for every match dominated indexed-grep latency
        on the ralph_self workload. This batches every row into
        one ``executemany`` under a single transaction so a
        100-match response makes one SQL round-trip instead of
        100.
        """
        if not rows:
            return
        payload: list[tuple[object, ...]] = [
            (
                row.evidence_id,
                row.path,
                row.start_line,
                row.end_line,
                row.content_hash,
                row.generation,
                row.source_tool,
                row.evidence_kind,
                row.created_at,
                1 if row.is_stale else 0,
                row.chunk_id,
                row.span_id,
            )
            for row in rows
        ]
        with self._transaction() as cur:
            cur.executemany(
                """
                INSERT INTO evidence (
                    evidence_id, path, start_line, end_line, content_hash,
                    generation, source_tool, evidence_kind, created_at, is_stale,
                    chunk_id, span_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(evidence_id) DO UPDATE SET
                    path=excluded.path,
                    start_line=excluded.start_line,
                    end_line=excluded.end_line,
                    content_hash=excluded.content_hash,
                    generation=excluded.generation,
                    source_tool=excluded.source_tool,
                    evidence_kind=excluded.evidence_kind,
                    created_at=excluded.created_at,
                    is_stale=excluded.is_stale,
                    chunk_id=excluded.chunk_id,
                    span_id=excluded.span_id
                """,
                payload,
            )

    def get_evidence(self, evidence_id: str) -> EvidenceRow | None:
        cur = self._conn.execute("SELECT * FROM evidence WHERE evidence_id = ?", (evidence_id,))
        row: sqlite3.Row | None = cur.fetchone()
        if row is None:
            return None
        return _row_to_evidence(row)

    # --- Structure (Phase 2: spans, symbols, edges) ---------------------

    def replace_structure_rows(
        self,
        *,
        path: str,
        spans: Sequence[SpanRow],
        symbols: Sequence[SymbolRow],
        edges: Sequence[EdgeRow],
    ) -> None:
        """Atomically replace the structure rows for ``path``.

        Deletes any prior spans/symbols/edges that point at this
        path's previous generation, then inserts the new ones. Used
        by the reindex pipeline after extraction; AC-06 requires that
        changed-file reindex leaves no stale graph rows behind.
        """
        with self._transaction() as cur:
            cur.execute("DELETE FROM spans WHERE path = ?", (path,))
            cur.execute("DELETE FROM symbols WHERE path = ?", (path,))
            cur.execute("DELETE FROM edges WHERE path = ?", (path,))
            for span_row in spans:
                cur.execute(
                    """
                    INSERT INTO spans (
                        span_id, path, start_line, start_col,
                        end_line, end_col, kind, symbol_id,
                        content_hash, generation
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(span_id) DO UPDATE SET
                        start_line=excluded.start_line,
                        start_col=excluded.start_col,
                        end_line=excluded.end_line,
                        end_col=excluded.end_col,
                        kind=excluded.kind,
                        symbol_id=excluded.symbol_id,
                        content_hash=excluded.content_hash,
                        generation=excluded.generation
                    """,
                    (
                        span_row.span_id,
                        span_row.path,
                        span_row.start_line,
                        span_row.start_col,
                        span_row.end_line,
                        span_row.end_col,
                        span_row.kind,
                        span_row.symbol_id,
                        span_row.content_hash,
                        span_row.generation,
                    ),
                )
            for symbol_row in symbols:
                cur.execute(
                    """
                    INSERT INTO symbols (
                        symbol_id, name, qualified_name, kind, path,
                        span_id, language, extracted_from,
                        confidence, generation
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(symbol_id) DO UPDATE SET
                        name=excluded.name,
                        qualified_name=excluded.qualified_name,
                        kind=excluded.kind,
                        path=excluded.path,
                        span_id=excluded.span_id,
                        language=excluded.language,
                        extracted_from=excluded.extracted_from,
                        confidence=excluded.confidence,
                        generation=excluded.generation
                    """,
                    (
                        symbol_row.symbol_id,
                        symbol_row.name,
                        symbol_row.qualified_name,
                        symbol_row.kind,
                        symbol_row.path,
                        symbol_row.span_id,
                        symbol_row.language,
                        symbol_row.extracted_from,
                        symbol_row.confidence,
                        symbol_row.generation,
                    ),
                )
            for edge_row in edges:
                cur.execute(
                    """
                    INSERT INTO edges (
                        edge_id, source_id, target_id, relation,
                        path, span_id, provenance, confidence,
                        reason, generation
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(edge_id) DO UPDATE SET
                        source_id=excluded.source_id,
                        target_id=excluded.target_id,
                        relation=excluded.relation,
                        path=excluded.path,
                        span_id=excluded.span_id,
                        provenance=excluded.provenance,
                        confidence=excluded.confidence,
                        reason=excluded.reason,
                        generation=excluded.generation
                    """,
                    (
                        edge_row.edge_id,
                        edge_row.source_id,
                        edge_row.target_id,
                        edge_row.relation,
                        edge_row.path,
                        edge_row.span_id,
                        edge_row.provenance,
                        edge_row.confidence,
                        edge_row.reason,
                        edge_row.generation,
                    ),
                )

    def iter_spans(self, path: str | None = None) -> Iterator[SpanRow]:
        if path is None:
            cur = self._conn.execute("SELECT * FROM spans ORDER BY path, start_line, start_col")
        else:
            cur = self._conn.execute(
                "SELECT * FROM spans WHERE path = ? ORDER BY start_line, start_col",
                (path,),
            )
        rows = cast(
            "list[sqlite3.Row]", cur.fetchall()
        )  # cast-policy: seam: structural boundary (sqlite Row / lazy module attr / protocol conferee)
        for span_row in rows:
            yield _row_to_span(span_row)

    def get_span(self, span_id: str) -> SpanRow | None:
        """Return the unique ``SpanRow`` for ``span_id`` or ``None``."""
        cur = self._conn.execute("SELECT * FROM spans WHERE span_id = ?", (span_id,))
        row: sqlite3.Row | None = cur.fetchone()
        if row is None:
            return None
        return _row_to_span(row)

    def find_symbols(
        self,
        *,
        name: str | None = None,
        qualified_name: str | None = None,
        path: str | None = None,
    ) -> list[SymbolRow]:
        """Return symbols filtered by ``name`` / ``qualified_name`` / ``path``.

        The query is conjunctive (every filter must match). Empty
        filters are ignored so callers can pass just one selector
        without supplying the others. Multiple symbols may match the
        same ``qualified_name`` (e.g. nested functions in different
        scopes), so this returns a list rather than a single row;
        callers must disambiguate by path or generation when needed.
        """
        clauses: list[str] = []
        params: list[object] = []
        if name is not None:
            clauses.append("name = ?")
            params.append(name)
        if qualified_name is not None:
            clauses.append("qualified_name = ?")
            params.append(qualified_name)
        if path is not None:
            clauses.append("path = ?")
            params.append(path)
        sql = "SELECT * FROM symbols"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY path, qualified_name, kind"
        cur = self._conn.execute(sql, tuple(params))
        rows = cast(
            "list[sqlite3.Row]", cur.fetchall()
        )  # cast-policy: seam: structural boundary (sqlite Row / lazy module attr / protocol conferee)
        return [_row_to_symbol(row) for row in rows]

    def iter_symbols(self, path: str | None = None) -> Iterator[SymbolRow]:
        if path is None:
            cur = self._conn.execute("SELECT * FROM symbols ORDER BY path, qualified_name")
        else:
            cur = self._conn.execute(
                "SELECT * FROM symbols WHERE path = ? ORDER BY qualified_name",
                (path,),
            )
        rows = cast(
            "list[sqlite3.Row]", cur.fetchall()
        )  # cast-policy: seam: structural boundary (sqlite Row / lazy module attr / protocol conferee)
        for sym_row in rows:
            yield _row_to_symbol(sym_row)

    def iter_edges(
        self,
        *,
        path: str | None = None,
        relation: str | None = None,
    ) -> Iterator[EdgeRow]:
        sql = "SELECT * FROM edges"
        params: tuple[object, ...] = ()
        clauses: list[str] = []
        if path is not None:
            clauses.append("path = ?")
            params = (*params, path)
        if relation is not None:
            clauses.append("relation = ?")
            params = (*params, relation)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY path, relation, source_id, target_id"
        cur = self._conn.execute(sql, params)
        rows = cast(
            "list[sqlite3.Row]", cur.fetchall()
        )  # cast-policy: seam: structural boundary (sqlite Row / lazy module attr / protocol conferee)
        for edge_row in rows:
            yield _row_to_edge(edge_row)

    def count_structure_rows(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for table in ("spans", "symbols", "edges"):
            cur = self._conn.execute(f"SELECT COUNT(*) FROM {table}")
            row: sqlite3.Row | None = cur.fetchone()
            counts[table] = _row_int_opt(row, 0) if row is not None else 0
        return counts

    # --- Dirty paths --------------------------------------------------

    def mark_dirty(
        self,
        path: str,
        *,
        reason: str,
        source_tool: str,
        now: float | None = None,
    ) -> None:
        """Persist ``path`` in the dirty queue. Idempotent."""
        normalized = normalize_index_path(path)
        marked_at = real_clock_seconds() if now is None else now
        with self._transaction() as cur:
            cur.execute(
                """
                INSERT INTO dirty_paths (
                    path, reason, marked_at, source_tool,
                    last_attempted_generation
                ) VALUES (?, ?, ?, ?, NULL)
                ON CONFLICT(path) DO UPDATE SET
                    reason=excluded.reason,
                    marked_at=excluded.marked_at,
                    source_tool=excluded.source_tool,
                    last_attempted_generation=NULL
                """,
                (normalized, reason, marked_at, source_tool),
            )

    def consume_dirty_paths(self) -> list[str]:
        """Atomically return + clear all currently dirty paths."""
        with self._transaction() as cur:
            cur.execute("SELECT path FROM dirty_paths")
            rows = cast(
                "list[sqlite3.Row]", cur.fetchall()
            )  # cast-policy: seam: structural boundary (sqlite Row / lazy module attr / protocol conferee)
            paths = [_row_str(row, "path") for row in rows]
            cur.execute("DELETE FROM dirty_paths")
            return paths

    def _remove_dirty_path(self, path: str) -> None:
        """Remove a single dirty path (used by selective reindex consume)."""
        try:
            normalized = normalize_index_path(path)
        except ValueError:
            return
        with self._transaction() as cur:
            cur.execute("DELETE FROM dirty_paths WHERE path = ?", (normalized,))

    def peek_dirty_paths(self) -> list[str]:
        cur = self._conn.execute("SELECT path FROM dirty_paths")
        rows = cast(
            "list[sqlite3.Row]", cur.fetchall()
        )  # cast-policy: seam: structural boundary (sqlite Row / lazy module attr / protocol conferee)
        return [_row_str(row, "path") for row in rows]

    # --- Settings -----------------------------------------------------

    def get_setting(self, key: str) -> str | None:
        # S-3 (wt-11): detect a cross-process atomic-rename swap
        # and reopen the connection before reading. The check is
        # a single ``stat()`` and only fires a reopen when the
        # inode actually changed; the steady-state cost is one
        # syscall per read.
        self.maybe_reopen_after_swap()
        cur = self._conn.execute("SELECT value FROM settings WHERE key = ?", (key,))
        row: sqlite3.Row | None = cur.fetchone()
        if row is None:
            return None
        value: object = row["value"]
        if value is None:
            return None
        return str(value)

    def set_setting(self, key: str, value: str) -> None:
        with self._transaction() as cur:
            cur.execute(
                """
                INSERT INTO settings (key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value
                """,
                (key, value),
            )
