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
import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import cast

from ralph.mcp.explore._store_class_content_cache import _ContentCacheMethods
from ralph.mcp.explore._store_types import (
    _DDL,
    _SCHEMA_MIGRATIONS,
    DEFAULT_BUSY_TIMEOUT_MS,
    DEFAULT_INDEX_DB,
    JOB_HISTORY_CAP,
    JOB_HISTORY_RETENTION_SECONDS,
    SCHEMA_VERSION,
    TOMBSTONE_CAP,
    TOMBSTONE_RETENTION_SECONDS,
    ChunkRow,
    EdgeRow,
    EvidenceRow,
    FileRow,
    SpanRow,
    SymbolRow,
    _column_exists,
    _is_add_column,
    _parse_add_column,
    _row_int_opt,
    _row_str,
    _row_to_edge,
    _row_to_evidence,
    _row_to_file,
    _row_to_span,
    _row_to_symbol,
    normalize_index_path,
    real_clock_seconds,
)

logger = logging.getLogger(__name__)


class ExploreStore(_ContentCacheMethods):
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
        self.index_dir.mkdir(parents=True, exist_ok=True)
        self._db_path = self.index_dir / DEFAULT_INDEX_DB
        self._busy_timeout_ms = busy_timeout_ms
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

    def _initialize(self) -> None:
        """Apply DDL + pragmas. Idempotent across reloads.

        AC-01: the on-disk ``settings.schema_version`` is compared to
        :data:`SCHEMA_VERSION`; a missing row or an older version
        triggers ``ALTER TABLE`` migrations to bring the database up
        to the current schema, or in the worst case a safe cold
        rebuild when an additive migration is not possible. The
        versions are pinned in ``_SCHEMA_MIGRATIONS`` so a future
        upgrade only needs to append one entry.
        """
        # Ponytail: pragmas (journal_mode, synchronous, busy_timeout,
        # foreign_keys) must be set OUTSIDE of an explicit transaction
        # because SQLite rejects ``PRAGMA synchronous`` (and friends)
        # inside a transaction.
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(f"PRAGMA busy_timeout={self._busy_timeout_ms}")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        with self._transaction() as cur:
            for stmt in _DDL:
                cur.execute(stmt)
        self._migrate_schema()

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Cursor]:
        """Run a short transaction with explicit BEGIN/COMMIT."""
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
            cur.close()

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

    # --- File-row access ----------------------------------------------

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
        cur = self._conn.execute(
            "SELECT * FROM files WHERE path = ?", (path,)
        )
        row: sqlite3.Row | None = cur.fetchone()
        if row is None:
            return None
        return _row_to_file(row)

    def iter_files(self) -> Iterator[FileRow]:
        cur = self._conn.execute("SELECT * FROM files WHERE is_deleted = 0")
        all_rows = cast("list[sqlite3.Row]", cur.fetchall())
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
        cur = self._conn.execute(
            "SELECT COUNT(*) FROM files WHERE is_deleted = 0"
        )
        row: sqlite3.Row | None = cur.fetchone()
        return _row_int_opt(row, 0) if row is not None else 0

    def count_deleted_files(self) -> int:
        """Return the count of file rows marked ``is_deleted=1``.

        Bounded: a single ``COUNT(*)`` aggregate. ``iter_files``
        filters out deleted rows, so this method is the only
        bounded way for callers to observe the deleted-row
        stale signal without materializing the entire table.
        """
        cur = self._conn.execute(
            "SELECT COUNT(*) FROM files WHERE is_deleted = 1"
        )
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
        cur = self._conn.execute(
            "SELECT EXISTS(SELECT 1 FROM files WHERE is_deleted = 1)"
        )
        row: sqlite3.Row | None = cur.fetchone()
        if row is None:
            return False
        return _row_int_opt(row, 0) > 0

    def delete_file_rows(self, path: str) -> None:
        """Remove file/chunk/evidence rows for ``path`` in current generation."""
        with self._transaction() as cur:
            cur.execute("DELETE FROM files WHERE path = ?", (path,))
            cur.execute("DELETE FROM chunks WHERE path = ?", (path,))
            cur.execute(
                "DELETE FROM chunks_fts WHERE path = ?", (path,)
            )
            cur.execute("DELETE FROM evidence WHERE path = ?", (path,))

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
            # FTS5 external-content table: replace the row by chunk_id.
            cur.execute(
                "DELETE FROM chunks_fts WHERE chunk_id = ?",
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

    def delete_chunks_for_path(self, path: str) -> None:
        """Delete all chunks and FTS rows for ``path``."""
        with self._transaction() as cur:
            cur.execute("DELETE FROM chunks WHERE path = ?", (path,))
            cur.execute("DELETE FROM chunks_fts WHERE path = ?", (path,))

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
        raw_results = cast("list[sqlite3.Row]", cur.fetchall())
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

    def get_evidence(self, evidence_id: str) -> EvidenceRow | None:
        cur = self._conn.execute(
            "SELECT * FROM evidence WHERE evidence_id = ?", (evidence_id,)
        )
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
            cur = self._conn.execute(
                "SELECT * FROM spans ORDER BY path, start_line, start_col"
            )
        else:
            cur = self._conn.execute(
                "SELECT * FROM spans WHERE path = ? "
                "ORDER BY start_line, start_col",
                (path,),
            )
        rows = cast("list[sqlite3.Row]", cur.fetchall())
        for span_row in rows:
            yield _row_to_span(span_row)

    def get_span(self, span_id: str) -> SpanRow | None:
        """Return the unique ``SpanRow`` for ``span_id`` or ``None``."""
        cur = self._conn.execute(
            "SELECT * FROM spans WHERE span_id = ?", (span_id,)
        )
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
        rows = cast("list[sqlite3.Row]", cur.fetchall())
        return [_row_to_symbol(row) for row in rows]

    def iter_symbols(self, path: str | None = None) -> Iterator[SymbolRow]:
        if path is None:
            cur = self._conn.execute(
                "SELECT * FROM symbols ORDER BY path, qualified_name"
            )
        else:
            cur = self._conn.execute(
                "SELECT * FROM symbols WHERE path = ? "
                "ORDER BY qualified_name",
                (path,),
            )
        rows = cast("list[sqlite3.Row]", cur.fetchall())
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
        rows = cast("list[sqlite3.Row]", cur.fetchall())
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
            rows = cast("list[sqlite3.Row]", cur.fetchall())
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
        rows = cast("list[sqlite3.Row]", cur.fetchall())
        return [_row_str(row, "path") for row in rows]

    # --- Settings -----------------------------------------------------

    def get_setting(self, key: str) -> str | None:
        cur = self._conn.execute(
            "SELECT value FROM settings WHERE key = ?", (key,)
        )
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

    # --- Job history (bounded) ----------------------------------------

    def record_job(
        self,
        *,
        job_id: str,
        generation: int,
        status: str,
        started_at: float,
        finished_at: float | None,
        files_seen: int,
        files_changed: int,
        files_failed: int,
        error_summary: str | None,
        now: float | None = None,
    ) -> None:
        now_seconds = time.time() if now is None else now
        with self._transaction() as cur:
            cur.execute(
                """
                INSERT INTO jobs (
                    job_id, generation, status, started_at, finished_at,
                    files_seen, files_changed, files_failed, error_summary
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    generation,
                    status,
                    started_at,
                    finished_at,
                    files_seen,
                    files_changed,
                    files_failed,
                    error_summary,
                ),
            )
            # Enforce bounded retention. Two queries because SQLite
            # does not allow ``DELETE ... LIMIT`` in older builds.
            cur.execute(
                """
                DELETE FROM jobs WHERE job_id IN (
                    SELECT job_id FROM jobs
                    ORDER BY started_at DESC
                    LIMIT -1 OFFSET ?
                )
                """,
                (JOB_HISTORY_CAP,),
            )
            cur.execute(
                "DELETE FROM jobs WHERE started_at < ?",
                (now_seconds - JOB_HISTORY_RETENTION_SECONDS,),
            )

    # Content-cache methods are provided by the
    # :class:`_ContentCacheMethods` mixin imported above. They
    # are documented in ``_store_class_content_cache.py``.

    def latest_job(self) -> sqlite3.Row | None:
        cur = self._conn.execute(
            "SELECT * FROM jobs ORDER BY started_at DESC LIMIT 1"
        )
        row: sqlite3.Row | None = cur.fetchone()
        return row

    # --- Evidence tombstones (bounded) -------------------------------

    def record_tombstone(
        self,
        *,
        evidence_id: str,
        path: str,
        start_line: int,
        end_line: int,
        content_hash: str,
        generation: int,
        stale_reason: str,
        stale_at: float,
        replacement_evidence_id: str | None,
        now: float | None = None,
    ) -> None:
        now_seconds = time.time() if now is None else now
        with self._transaction() as cur:
            # AC-05: tombstone identity is derived deterministically
            # from (path, content_hash, kind), so a delete-then-restore
            # cycle of the same bytes can produce the same evidence_id
            # on the next delete. The lifecycle must remain idempotent
            # to avoid ``IntegrityError`` on the primary-key collision.
            # An ON CONFLICT refreshes ``stale_at``/``stale_reason``/
            # ``replacement_evidence_id`` on the existing row so the
            # row count does not balloon and the most recent deletion
            # wins for lookup, while bounded retention still applies.
            cur.execute(
                """
                INSERT INTO evidence_tombstones (
                    evidence_id, path, start_line, end_line, content_hash,
                    generation, stale_reason, stale_at, replacement_evidence_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(evidence_id) DO UPDATE SET
                    stale_at=excluded.stale_at,
                    stale_reason=excluded.stale_reason,
                    replacement_evidence_id=excluded.replacement_evidence_id,
                    generation=excluded.generation
                """,
                (
                    evidence_id,
                    path,
                    start_line,
                    end_line,
                    content_hash,
                    generation,
                    stale_reason,
                    stale_at,
                    replacement_evidence_id,
                ),
            )
            cur.execute(
                """
                DELETE FROM evidence_tombstones WHERE evidence_id IN (
                    SELECT evidence_id FROM evidence_tombstones
                    ORDER BY stale_at DESC
                    LIMIT -1 OFFSET ?
                )
                """,
                (TOMBSTONE_CAP,),
            )
            cur.execute(
                "DELETE FROM evidence_tombstones WHERE stale_at < ?",
                (now_seconds - TOMBSTONE_RETENTION_SECONDS,),
            )

    def get_tombstone(self, evidence_id: str) -> sqlite3.Row | None:
        cur = self._conn.execute(
            """
            SELECT * FROM evidence_tombstones WHERE evidence_id = ?
            """,
            (evidence_id,),
        )
        row: sqlite3.Row | None = cur.fetchone()
        return row

    # --- Storage size -------------------------------------------------

    def index_storage_bytes(self) -> int:
        total = 0
        if self._db_path.exists():
            total += self._db_path.stat().st_size
        wal = self._db_path.with_suffix(".sqlite-wal")
        if wal.exists():
            total += wal.stat().st_size
        shm = self._db_path.with_suffix(".sqlite-shm")
        if shm.exists():
            total += shm.stat().st_size
        return total

    # --- Schema migration (AC-01) ------------------------------------

    def _migrate_schema(self) -> None:
        """Bring an existing SQLite file up to ``SCHEMA_VERSION``.

        Reads ``settings.schema_version``; if the value is missing or
        older than :data:`SCHEMA_VERSION`, the additive migration
        statements in :data:`_SCHEMA_MIGRATIONS` are executed. Each
        migration is itself idempotent (``ALTER TABLE ... ADD COLUMN``
        guarded by a schema introspection) so a re-open of a fully
        migrated database is a no-op. The recorded version is then
        pinned to ``SCHEMA_VERSION`` so the next open is also a no-op.
        """
        cur = self._conn.execute(
            "SELECT value FROM settings WHERE key = 'schema_version'"
        )
        row_obj: sqlite3.Row | None = cur.fetchone()
        on_disk: str = ""
        if row_obj is not None:
            cell_obj: object = row_obj["value"]
            on_disk = cell_obj if isinstance(cell_obj, str) else ""
        if on_disk == SCHEMA_VERSION:
            return
        known_migration_versions = {m[0] for m in _SCHEMA_MIGRATIONS}
        if on_disk and on_disk not in known_migration_versions:
            # Future / newer-than-known schema: refuse rather than
            # silently serve incompatible rows.
            raise RuntimeError(
                f"explore schema version {on_disk!r} is newer than "
                f"supported {SCHEMA_VERSION!r}; rebuild required"
            )
        for version, migration_sql in _SCHEMA_MIGRATIONS:
            if on_disk and on_disk >= version:
                continue
            with self._transaction() as cur:
                # Idempotency guard: ``ALTER TABLE ... ADD COLUMN`` is
                # not idempotent on SQLite and ``CREATE ... IF NOT
                # EXISTS`` already handles itself. We only run an
                # ADD COLUMN when the column is genuinely missing.
                if _is_add_column(migration_sql):
                    table, column = _parse_add_column(migration_sql)
                    if _column_exists(cur, table, column):
                        continue
                cur.execute(migration_sql)
        with self._transaction() as cur:
            cur.execute(
                "INSERT INTO settings (key, value) VALUES "
                "('schema_version', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (SCHEMA_VERSION,),
            )
