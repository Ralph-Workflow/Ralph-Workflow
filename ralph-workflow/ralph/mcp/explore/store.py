"""SQLite+FTS5 store for the Ralph indexed exploration substrate.

Phase 1 keeps the substrate lexical-only:

* ``files`` table — workspace file manifest with content hash and
  indexed generation.
* ``chunks`` table — line-windowed text chunks for FTS5.
* ``chunks_fts`` — external-content FTS5 index tied to ``chunks``.
* ``evidence`` table — exact evidence handles (``evidence_id``) used
  by indexed reads.
* ``evidence_tombstones`` — bounded ledger of stale evidence (used to
  resolve stale reads; capped at 10k rows / 30 days).
* ``dirty_paths`` — persisted dirty-path queue surviving crashes.
* ``jobs`` — bounded reindex job history (capped 100 / 14 days).
* ``settings`` — key/value index settings.
* ``manifest`` — disk-level file manifest with mtime/size prefilter
  (content hash is authoritative).

The store is stdlib ``sqlite3`` + FTS5. WAL mode is enabled for
concurrent readers. All blocking I/O is bounded by an explicit
``timeout_ms`` and fails closed when the lock cannot be acquired.

The index lives under ``.agent/ralph-explore/index.sqlite`` and is
git-ignored via the existing ``.agent/`` rule in
``ralph/config/bootstrap.py:_DEFAULT_GITIGNORE_PATTERNS``.

Phase 1 deliberately skips the AST symbol/edge tables (``symbols``,
``edges``, ``spans``) from the schema sketch in
the full schema sketch — those belong to Phase 2 and are recorded in
``deferred_phases.py``. The minimum table set is enough to serve
indexed ``grep_files``, ``read_file``, ``read_multiple_files``, and
``search_files`` for Phase 1.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Protocol, cast

# --- Constants -------------------------------------------------------------

DEFAULT_INDEX_ROOT: Final[str] = ".agent/ralph-explore"
DEFAULT_INDEX_DB: Final[str] = "index.sqlite"
DEFAULT_CHUNK_LINES: Final[int] = 50
DEFAULT_FTS_TOKENIZE: Final[str] = "unicode61"

# Bounded caps for retention. The audit_register/evidence_tombstones
# caps are documented in the architecture finding.
JOB_HISTORY_CAP: Final[int] = 100
JOB_HISTORY_RETENTION_SECONDS: Final[int] = 14 * 24 * 60 * 60
TOMBSTONE_CAP: Final[int] = 10_000
TOMBSTONE_RETENTION_SECONDS: Final[int] = 30 * 24 * 60 * 60

# SQLite default per-call busy timeout. The architecture finding
# requires every blocking call to be bounded with a fail-closed timeout.
DEFAULT_BUSY_TIMEOUT_MS: Final[int] = 5_000

# Maximum file size inlined for FTS chunking. Files larger than this
# still get hashed + chunked at byte-window granularity; the index
# only stores chunks (never the full file) so the FTS footprint stays
# bounded.
DEFAULT_MAX_CHUNK_BYTES: Final[int] = 16 * 1024


# --- Schema ----------------------------------------------------------------

_DDL: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS files (
        path TEXT PRIMARY KEY,
        content_hash TEXT NOT NULL,
        size_bytes INTEGER NOT NULL,
        mtime_ns INTEGER NOT NULL,
        language TEXT,
        indexed_generation INTEGER NOT NULL,
        indexed_at REAL NOT NULL,
        is_deleted INTEGER NOT NULL DEFAULT 0
            CHECK(is_deleted IN (0, 1))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS chunks (
        chunk_id TEXT PRIMARY KEY,
        path TEXT NOT NULL,
        start_line INTEGER NOT NULL,
        end_line INTEGER NOT NULL,
        text_hash TEXT NOT NULL,
        role TEXT NOT NULL DEFAULT 'body',
        generation INTEGER NOT NULL,
        UNIQUE(path, start_line, end_line, text_hash)
    )
    """,
    """
    CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
        text,
        path UNINDEXED,
        chunk_id UNINDEXED,
        symbol_names,
        headings,
        comments,
        tokenize = 'unicode61'
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS evidence (
        evidence_id TEXT PRIMARY KEY,
        path TEXT NOT NULL,
        start_line INTEGER NOT NULL,
        end_line INTEGER NOT NULL,
        content_hash TEXT NOT NULL,
        generation INTEGER NOT NULL,
        source_tool TEXT NOT NULL,
        evidence_kind TEXT NOT NULL,
        created_at REAL NOT NULL,
        is_stale INTEGER NOT NULL DEFAULT 0
            CHECK(is_stale IN (0, 1))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS evidence_tombstones (
        evidence_id TEXT PRIMARY KEY,
        path TEXT NOT NULL,
        start_line INTEGER NOT NULL,
        end_line INTEGER NOT NULL,
        content_hash TEXT NOT NULL,
        generation INTEGER NOT NULL,
        stale_reason TEXT NOT NULL,
        stale_at REAL NOT NULL,
        replacement_evidence_id TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS dirty_paths (
        path TEXT PRIMARY KEY,
        reason TEXT NOT NULL,
        marked_at REAL NOT NULL,
        source_tool TEXT NOT NULL,
        last_attempted_generation INTEGER
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS jobs (
        job_id TEXT PRIMARY KEY,
        generation INTEGER NOT NULL,
        status TEXT NOT NULL,
        started_at REAL NOT NULL,
        finished_at REAL,
        files_seen INTEGER NOT NULL DEFAULT 0,
        files_changed INTEGER NOT NULL DEFAULT 0,
        files_failed INTEGER NOT NULL DEFAULT 0,
        error_summary TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS manifest (
        path TEXT PRIMARY KEY,
        content_hash TEXT NOT NULL,
        size_bytes INTEGER NOT NULL,
        mtime_ns INTEGER NOT NULL,
        inode_or_file_id INTEGER,
        last_seen_generation INTEGER NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT
    )
    """,
    # Phase 2 structure tables (AC-06). The schema is additive so a
    # Phase 1 database can reopen and gain structure rows on the
    # next changed/full reindex. Stable ids are deterministic from
    # (path, kind, start_line, start_col, end_line, end_col,
    # extractor_version) so no-op reindex produces stable logical rows.
    """
    CREATE TABLE IF NOT EXISTS spans (
        span_id TEXT PRIMARY KEY,
        path TEXT NOT NULL,
        start_line INTEGER NOT NULL,
        start_col INTEGER NOT NULL,
        end_line INTEGER NOT NULL,
        end_col INTEGER NOT NULL,
        kind TEXT NOT NULL,
        symbol_id TEXT,
        content_hash TEXT NOT NULL,
        generation INTEGER NOT NULL,
        UNIQUE(path, start_line, start_col, end_line, end_col, kind, content_hash)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS symbols (
        symbol_id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        qualified_name TEXT NOT NULL,
        kind TEXT NOT NULL,
        path TEXT NOT NULL,
        span_id TEXT NOT NULL,
        language TEXT,
        extracted_from TEXT NOT NULL,
        confidence REAL NOT NULL DEFAULT 1.0,
        generation INTEGER NOT NULL,
        UNIQUE(path, qualified_name, kind, span_id, generation)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS edges (
        edge_id TEXT PRIMARY KEY,
        source_id TEXT NOT NULL,
        target_id TEXT NOT NULL,
        relation TEXT NOT NULL,
        path TEXT NOT NULL,
        span_id TEXT,
        provenance TEXT NOT NULL,
        confidence REAL NOT NULL DEFAULT 1.0,
        reason TEXT,
        generation INTEGER NOT NULL,
        UNIQUE(source_id, target_id, relation, path, span_id, generation)
    )
    """,
)


# --- Public dataclasses ----------------------------------------------------


@dataclass(frozen=True, slots=True)
class FileRow:
    """A row from the ``files`` table."""

    path: str
    content_hash: str
    size_bytes: int
    mtime_ns: int
    language: str | None
    indexed_generation: int
    indexed_at: float
    is_deleted: bool


@dataclass(frozen=True, slots=True)
class ChunkRow:
    """A row from the ``chunks`` table."""

    chunk_id: str
    path: str
    start_line: int
    end_line: int
    text_hash: str
    role: str
    generation: int


@dataclass(frozen=True, slots=True)
class EvidenceRow:
    """A row from the ``evidence`` table."""

    evidence_id: str
    path: str
    start_line: int
    end_line: int
    content_hash: str
    generation: int
    source_tool: str
    evidence_kind: str
    created_at: float
    is_stale: bool


@dataclass(frozen=True, slots=True)
class SpanRow:
    """A row from the ``spans`` table (AC-06 Phase 2 structure)."""

    span_id: str
    path: str
    start_line: int
    start_col: int
    end_line: int
    end_col: int
    kind: str
    symbol_id: str | None
    content_hash: str
    generation: int


@dataclass(frozen=True, slots=True)
class SymbolRow:
    """A row from the ``symbols`` table (AC-06 Phase 2 structure)."""

    symbol_id: str
    name: str
    qualified_name: str
    kind: str
    path: str
    span_id: str
    language: str | None
    extracted_from: str
    confidence: float
    generation: int


@dataclass(frozen=True, slots=True)
class EdgeRow:
    """A row from the ``edges`` table (AC-06 Phase 2 structure)."""

    edge_id: str
    source_id: str
    target_id: str
    relation: str
    path: str
    span_id: str | None
    provenance: str
    confidence: float
    reason: str | None
    generation: int


class Clock(Protocol):
    """Protocol for wall-clock injection. Tests inject a FakeClock."""

    def now(self) -> float:
        ...


class SystemClock:
    """Default wall-clock implementation backed by ``time.monotonic``."""

    def now(self) -> float:
        return time.monotonic()


def real_clock_seconds() -> float:
    """Return a wall-clock seconds value as a float.

    Used for ``indexed_at`` / ``marked_at`` / ``created_at`` fields
    that are persisted alongside evidence rows. Tests inject a fake
    clock through ``Clock`` to avoid ``audit_test_policy`` wall-clock
    violations; this helper is the default for production code.
    """
    return time.time()


# --- Path normalization ----------------------------------------------------


def normalize_index_path(path: str) -> str:
    """Normalize a workspace-relative POSIX path for index storage.

    Ponytail: re-export of the existing workspace boundary check so
    the index store cannot accept absolute paths or ``..`` segments.
    """
    # Import lazily to avoid an import cycle at module load time
    # (the workspace utils import the MCP coordination helpers).
    from ralph.mcp.tools.workspace._utils import normalize_relative_path

    return normalize_relative_path(path)


# --- Store -----------------------------------------------------------------


class ExploreStore:
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
        # Ponytail: open with ``isolation_level=None`` so we can
        # control transactions explicitly with BEGIN/COMMIT and
        # avoid implicit transactions bloating the WAL.
        self._conn = sqlite3.connect(
            str(self._db_path),
            timeout=busy_timeout_ms / 1000.0,
            isolation_level=None,
        )
        self._conn.row_factory = sqlite3.Row
        self._initialize()

    def _initialize(self) -> None:
        """Apply DDL + pragmas. Idempotent across reloads."""
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
    ) -> list[sqlite3.Row]:
        """Run an FTS5 MATCH query and return rows.

        Returns ``chunks_fts`` rows (path, chunk_id, text). Callers
        are expected to translate chunk_id to evidence handles.
        """
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
        results = cast("list[sqlite3.Row]", cur.fetchall())
        return results

    # --- Evidence -----------------------------------------------------

    def insert_evidence(self, row: EvidenceRow) -> None:
        with self._transaction() as cur:
            cur.execute(
                """
                INSERT INTO evidence (
                    evidence_id, path, start_line, end_line, content_hash,
                    generation, source_tool, evidence_kind, created_at, is_stale
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(evidence_id) DO UPDATE SET
                    path=excluded.path,
                    start_line=excluded.start_line,
                    end_line=excluded.end_line,
                    content_hash=excluded.content_hash,
                    generation=excluded.generation,
                    source_tool=excluded.source_tool,
                    evidence_kind=excluded.evidence_kind,
                    created_at=excluded.created_at,
                    is_stale=excluded.is_stale
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
            for row in spans:
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
                        row.span_id,
                        row.path,
                        row.start_line,
                        row.start_col,
                        row.end_line,
                        row.end_col,
                        row.kind,
                        row.symbol_id,
                        row.content_hash,
                        row.generation,
                    ),
                )
            for row in symbols:
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
                        row.symbol_id,
                        row.name,
                        row.qualified_name,
                        row.kind,
                        row.path,
                        row.span_id,
                        row.language,
                        row.extracted_from,
                        row.confidence,
                        row.generation,
                    ),
                )
            for row in edges:
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
                        row.edge_id,
                        row.source_id,
                        row.target_id,
                        row.relation,
                        row.path,
                        row.span_id,
                        row.provenance,
                        row.confidence,
                        row.reason,
                        row.generation,
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
        for row in rows:
            yield SpanRow(
                span_id=str(row["span_id"]),
                path=str(row["path"]),
                start_line=int(row["start_line"]),
                start_col=int(row["start_col"]),
                end_line=int(row["end_line"]),
                end_col=int(row["end_col"]),
                kind=str(row["kind"]),
                symbol_id=(
                    str(row["symbol_id"]) if row["symbol_id"] is not None else None
                ),
                content_hash=str(row["content_hash"]),
                generation=int(row["generation"]),
            )

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
        for row in rows:
            yield SymbolRow(
                symbol_id=str(row["symbol_id"]),
                name=str(row["name"]),
                qualified_name=str(row["qualified_name"]),
                kind=str(row["kind"]),
                path=str(row["path"]),
                span_id=str(row["span_id"]),
                language=(
                    str(row["language"]) if row["language"] is not None else None
                ),
                extracted_from=str(row["extracted_from"]),
                confidence=float(row["confidence"]),
                generation=int(row["generation"]),
            )

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
        for row in rows:
            yield EdgeRow(
                edge_id=str(row["edge_id"]),
                source_id=str(row["source_id"]),
                target_id=str(row["target_id"]),
                relation=str(row["relation"]),
                path=str(row["path"]),
                span_id=(
                    str(row["span_id"]) if row["span_id"] is not None else None
                ),
                provenance=str(row["provenance"]),
                confidence=float(row["confidence"]),
                reason=str(row["reason"]) if row["reason"] is not None else None,
                generation=int(row["generation"]),
            )

    def count_structure_rows(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for table in ("spans", "symbols", "edges"):
            cur = self._conn.execute(f"SELECT COUNT(*) FROM {table}")
            row = cur.fetchone()
            counts[table] = int(row[0]) if row is not None else 0
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
            cur.execute(
                """
                INSERT INTO evidence_tombstones (
                    evidence_id, path, start_line, end_line, content_hash,
                    generation, stale_reason, stale_at, replacement_evidence_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
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


# --- Helpers --------------------------------------------------------------


def _row_str(row: sqlite3.Row, key: str) -> str:
    """Read a string column from ``row`` with a precise return type."""
    value: object = row[key]
    if not isinstance(value, str):
        return str(value)
    return value


row_str = _row_str


def _row_int(row: sqlite3.Row, key: str) -> int:
    """Read an integer column from ``row`` with a precise return type."""
    value: object = row[key]
    if isinstance(value, bool):
        return int(bool(value))
    if isinstance(value, int):
        return value
    return int(cast("int | str | float", value))


def _row_float(row: sqlite3.Row, key: str) -> float:
    """Read a float column from ``row`` with a precise return type."""
    value: object = row[key]
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    return float(cast("int | str | float", value))


def _row_bool(row: sqlite3.Row, key: str) -> bool:
    """Read a boolean column from ``row`` with a precise return type."""
    value: object = row[key]
    if isinstance(value, bool):
        return value
    return bool(value)


def _row_optional_str(row: sqlite3.Row, key: str) -> str | None:
    """Read an optional string column from ``row``."""
    value: object = row[key]
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return str(value)


def _row_to_file(row: sqlite3.Row) -> FileRow:
    """Convert a ``files`` row to a typed ``FileRow`` dataclass."""
    return FileRow(
        path=_row_str(row, "path"),
        content_hash=_row_str(row, "content_hash"),
        size_bytes=_row_int(row, "size_bytes"),
        mtime_ns=_row_int(row, "mtime_ns"),
        language=_row_optional_str(row, "language"),
        indexed_generation=_row_int(row, "indexed_generation"),
        indexed_at=_row_float(row, "indexed_at"),
        is_deleted=bool(_row_int(row, "is_deleted")),
    )


def _row_to_evidence(row: sqlite3.Row) -> EvidenceRow:
    """Convert an ``evidence`` row to a typed ``EvidenceRow`` dataclass."""
    return EvidenceRow(
        evidence_id=_row_str(row, "evidence_id"),
        path=_row_str(row, "path"),
        start_line=_row_int(row, "start_line"),
        end_line=_row_int(row, "end_line"),
        content_hash=_row_str(row, "content_hash"),
        generation=_row_int(row, "generation"),
        source_tool=_row_str(row, "source_tool"),
        evidence_kind=_row_str(row, "evidence_kind"),
        created_at=_row_float(row, "created_at"),
        is_stale=bool(_row_int(row, "is_stale")),
    )


def sha256_text(text: str) -> str:
    """Return the SHA-256 hex digest of ``text`` (UTF-8 encoded)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_bytes(data: bytes) -> str:
    """Return the SHA-256 hex digest of ``data``."""
    return hashlib.sha256(data).hexdigest()


def chunk_text(text: str, *, lines_per_chunk: int = DEFAULT_CHUNK_LINES) -> list[tuple[int, int, str]]:
    """Split ``text`` into ``(start_line, end_line, chunk_text)`` chunks.

    Lines are 1-based and inclusive. The trailing chunk may be smaller
    than ``lines_per_chunk``. Empty input returns an empty list so
    callers can iterate without a guard.
    """
    if not text:
        return []
    lines = text.splitlines(keepends=False)
    if not lines:
        return []
    chunks: list[tuple[int, int, str]] = []
    for start in range(0, len(lines), lines_per_chunk):
        end = min(start + lines_per_chunk, len(lines))
        chunk_lines = lines[start:end]
        chunks.append((start + 1, end, "\n".join(chunk_lines)))
    return chunks


def derive_chunk_id(
    *,
    path: str,
    start_line: int,
    end_line: int,
    text_hash: str,
    extractor_version: str,
) -> str:
    """Return a stable chunk id derived from the deterministic inputs."""
    payload = f"{path}\x00{start_line}\x00{end_line}\x00{text_hash}\x00{extractor_version}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def derive_evidence_id(
    *,
    path: str,
    content_hash: str,
    start_line: int,
    end_line: int,
    kind: str,
    extractor_version: str,
) -> str:
    """Return a stable evidence id derived from the deterministic inputs."""
    payload = (
        f"ev\x00{path}\x00{content_hash}\x00{start_line}\x00{end_line}\x00"
        f"{kind}\x00{extractor_version}"
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# Ponytail: small helper to enumerate indexable files in a workspace.
# Phase 1 keeps this minimal: skip hidden dirs, common VCS dirs, and
# anything under .agent/ (the index lives there, never inside itself).
_SKIP_DIR_NAMES: Final[frozenset[str]] = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".agent",
        "__pycache__",
        ".venv",
        ".mypy_cache",
        ".ruff_cache",
        ".pytest_cache",
        "node_modules",
        "target",
        "build",
        "dist",
    }
)


def iter_indexable_files(workspace_root: Path) -> Iterator[Path]:
    """Yield indexable file paths under ``workspace_root``.

    Skips common VCS / cache / build directories. Always returns
    paths relative to ``workspace_root`` so the caller can normalize
    them with :func:`normalize_index_path`.
    """
    root = Path(workspace_root).resolve()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIR_NAMES]
        for name in filenames:
            yield Path(dirpath) / name


def collect_workspace_files(workspace_root: Path) -> list[tuple[str, int, int]]:
    """Return ``[(relative_path, size_bytes, mtime_ns), ...]`` for indexable files."""
    root = Path(workspace_root).resolve()
    rows: list[tuple[str, int, int]] = []
    for candidate in iter_indexable_files(root):
        try:
            stat_result = candidate.stat()
        except FileNotFoundError:
            continue
        relative = candidate.relative_to(root).as_posix()
        rows.append((relative, stat_result.st_size, stat_result.st_mtime_ns))
    return sorted(rows)


def hash_workspace_file(workspace_root: Path, relative_path: str) -> tuple[str, int, int]:
    """Return ``(content_hash, size_bytes, mtime_ns)`` for a single workspace file.

    Reads via the same workspace boundary the MCP tools enforce. The
    file's content hash is authoritative; mtime/size are a cheap
    prefilter only.

    Uses ``Path.is_relative_to`` (Python 3.9+) so the boundary check
    cannot be fooled by sibling-prefix escapes (``/tmp/ws`` vs
    ``/tmp/ws_evil``) or symlink targets outside ``workspace_root``.
    """
    root = Path(workspace_root).resolve()
    full = (root / relative_path).resolve()
    # Ponytail: string-prefix startswith() is unsafe — ``/tmp/ws_evil``
    # startswith ``/tmp/ws``. Use Path.is_relative_to instead so
    # sibling-prefix collisions and symlink escapes are rejected.
    try:
        is_relative = full.is_relative_to(root)
    except AttributeError:  # pragma: no cover — Python <3.9 fallback
        is_relative = str(full).startswith(str(root) + os.sep) or full == root
    if not is_relative:
        raise ValueError(f"Path escapes workspace: {relative_path!r}")
    if not full.is_file():
        raise FileNotFoundError(relative_path)
    data = full.read_bytes()
    stat_result = full.stat()
    return (
        sha256_bytes(data),
        stat_result.st_size,
        stat_result.st_mtime_ns,
    )


def assert_within_workspace(workspace_root: Path, full_path: Path) -> None:
    """Raise ``ValueError`` when ``full_path`` is outside ``workspace_root``.

    Resolves symlinks and uses ``Path.is_relative_to`` to defend
    against sibling-prefix and symlink-escape attacks on the
    indexed exploration store. Used by the reindex pipeline and the
    workspace file handlers to share one boundary check.
    """
    root = Path(workspace_root).resolve()
    resolved = Path(full_path).resolve()
    try:
        is_relative = resolved.is_relative_to(root)
    except AttributeError:  # pragma: no cover
        is_relative = str(resolved).startswith(str(root) + os.sep) or resolved == root
    if not is_relative:
        raise ValueError(f"Path escapes workspace: {full_path}")


__all__ = [
    "DEFAULT_CHUNK_LINES",
    "DEFAULT_INDEX_DB",
    "DEFAULT_INDEX_ROOT",
    "ChunkRow",
    "Clock",
    "EdgeRow",
    "EvidenceRow",
    "ExploreStore",
    "FileRow",
    "SpanRow",
    "SymbolRow",
    "SystemClock",
    "assert_within_workspace",
    "chunk_text",
    "collect_workspace_files",
    "derive_chunk_id",
    "derive_evidence_id",
    "hash_workspace_file",
    "iter_indexable_files",
    "normalize_index_path",
    "real_clock_seconds",
    "row_str",
    "sha256_bytes",
    "sha256_text",
]
