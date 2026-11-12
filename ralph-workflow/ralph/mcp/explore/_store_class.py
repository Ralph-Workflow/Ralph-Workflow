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


_FTS_GLOB_PAGE_ROWS = 256

_FTS_SEARCH_SQL = """
    SELECT path, chunk_id, snippet(chunks_fts, 0, '', '', '...', 8) AS snippet
    FROM chunks_fts
    WHERE chunks_fts MATCH ?
      AND (? = '' OR path = ? OR substr(path, 1, ?) = ?)
    ORDER BY bm25(chunks_fts)
    LIMIT ?
"""

_FTS_SEARCH_GREP_SQL = """
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
      AND (? = '' OR fts.path = ? OR substr(fts.path, 1, ?) = ?)
    ORDER BY bm25(chunks_fts)
    LIMIT ?
"""


class ExploreStore(
    _ContentCacheMethods,
    _FileRowMethods,
    _InitializeMethods,
    _RetentionMethods,
):
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
        self._db_inode: int | None = None
        self._in_transaction: bool = False
        try:
            self._db_inode = self._db_path.stat().st_ino
        except OSError:
            self._db_inode = None
        self._conn = sqlite3.connect(
            str(self._db_path),
            timeout=busy_timeout_ms / 1000.0,
            isolation_level=None,
            check_same_thread=False,
        )
        self._conn.row_factory = sqlite3.Row
        self._initialize()

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Cursor]:
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

    @property
    def db_path(self) -> Path:
        return self._db_path

    def close(self) -> None:
        self._conn.close()

    def reopen(self) -> None:
        with suppress(sqlite3.ProgrammingError):
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
        try:
            self._db_inode = self._db_path.stat().st_ino
        except OSError:
            self._db_inode = None

    def maybe_reopen_after_swap(self) -> bool:
        try:
            current_inode = self._db_path.stat().st_ino
        except OSError:
            return False
        if current_inode == self._db_inode:
            return False
        self.reopen()
        return True

    def upsert_chunk(self, chunk: ChunkRow, text: str) -> None:
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
        return self._scoped_fts_rows(
            _FTS_SEARCH_SQL,
            query,
            limit=limit,
            path_prefix=path_prefix,
            include_globs=include_globs,
            exclude_globs=exclude_globs,
        )

    def fts_search_grep(
        self,
        query: str,
        *,
        limit: int = 100,
        path_prefix: str | None = None,
        include_globs: Sequence[str] | None = None,
        exclude_globs: Sequence[str] | None = None,
    ) -> list[sqlite3.Row]:
        return self._scoped_fts_rows(
            _FTS_SEARCH_GREP_SQL,
            query,
            limit=limit,
            path_prefix=path_prefix,
            include_globs=include_globs,
            exclude_globs=exclude_globs,
        )

    def _scoped_fts_rows(
        self,
        sql: str,
        query: str,
        *,
        limit: int,
        path_prefix: str | None,
        include_globs: Sequence[str] | None,
        exclude_globs: Sequence[str] | None,
    ) -> list[sqlite3.Row]:
        from ralph.mcp.explore.path_filter import (
            compile_path_filter,
        )

        prefix = (path_prefix or "").rstrip("/")
        glob_filter = compile_path_filter(
            path_prefix=None,
            include_globs=include_globs,
            exclude_globs=exclude_globs,
        )
        dir_prefix = prefix + "/"
        params = (
            query,
            prefix,
            prefix,
            len(dir_prefix),
            dir_prefix,
            limit if glob_filter is None else -1,
        )
        cur = self._conn.execute(sql, params)
        try:
            if glob_filter is None:
                return cast("list[sqlite3.Row]", cur.fetchall())
            selected: list[sqlite3.Row] = []
            while len(selected) < limit:
                page = cast("list[sqlite3.Row]", cur.fetchmany(_FTS_GLOB_PAGE_ROWS))
                if not page:
                    break
                for row in page:
                    path_obj: object = row["path"]
                    if glob_filter(str(path_obj) if path_obj is not None else ""):
                        selected.append(row)
                        if len(selected) >= limit:
                            break
            return selected
        finally:
            cur.close()

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

    def replace_structure_rows(
        self,
        *,
        path: str,
        spans: Sequence[SpanRow],
        symbols: Sequence[SymbolRow],
        edges: Sequence[EdgeRow],
    ) -> None:
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
        rows = cast("list[sqlite3.Row]", cur.fetchall())
        for span_row in rows:
            yield _row_to_span(span_row)

    def get_span(self, span_id: str) -> SpanRow | None:
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
            cur = self._conn.execute("SELECT * FROM symbols ORDER BY path, qualified_name")
        else:
            cur = self._conn.execute(
                "SELECT * FROM symbols WHERE path = ? ORDER BY qualified_name",
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

    def mark_dirty(
        self,
        path: str,
        *,
        reason: str,
        source_tool: str,
        now: float | None = None,
    ) -> None:
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
        with self._transaction() as cur:
            cur.execute("SELECT path FROM dirty_paths")
            rows = cast("list[sqlite3.Row]", cur.fetchall())
            paths = [_row_str(row, "path") for row in rows]
            cur.execute("DELETE FROM dirty_paths")
            return paths

    def _remove_dirty_path(self, path: str) -> None:
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

    def get_setting(self, key: str) -> str | None:
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
