from __future__ import annotations

import hashlib
import os
import re as _re_migration
import sqlite3
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Protocol, cast

from ralph.mcp.explore._store_types_payload import (
    ContentCacheChunk,
    ContentCachePayload,
)

DEFAULT_INDEX_ROOT: Final[str] = ".agent/ralph-explore"
DEFAULT_INDEX_DB: Final[str] = "index.sqlite"
DEFAULT_CHUNK_LINES: Final[int] = 50
DEFAULT_FTS_TOKENIZE: Final[str] = "unicode61"

SCHEMA_VERSION: Final[str] = "explore-v2"

JOB_HISTORY_CAP: Final[int] = 100
JOB_HISTORY_RETENTION_SECONDS: Final[int] = 14 * 24 * 60 * 60
TOMBSTONE_CAP: Final[int] = 10_000
TOMBSTONE_RETENTION_SECONDS: Final[int] = 30 * 24 * 60 * 60

DEFAULT_BUSY_TIMEOUT_MS: Final[int] = 5_000

DEFAULT_MAX_CHUNK_BYTES: Final[int] = 16 * 1024


_SCHEMA_MIGRATIONS: tuple[tuple[str, str], ...] = (
    (
        "explore-v2",
        "CREATE INDEX IF NOT EXISTS idx_edges_path ON edges(path)",
    ),
    (
        "explore-v2",
        "ALTER TABLE chunks ADD COLUMN fts_rowid INTEGER",
    ),
    (
        "explore-v1",
        "ALTER TABLE evidence ADD COLUMN chunk_id TEXT",
    ),
    (
        "explore-v1",
        "ALTER TABLE evidence ADD COLUMN span_id TEXT",
    ),
)


_ADD_COLUMN_RE = _re_migration.compile(
    r"^\s*ALTER\s+TABLE\s+(\w+)\s+ADD\s+COLUMN\s+(\w+)\b",
    _re_migration.IGNORECASE,
)


def _is_add_column(sql: str) -> bool:
    return bool(_ADD_COLUMN_RE.match(sql))


def _parse_add_column(sql: str) -> tuple[str, str]:
    match = _ADD_COLUMN_RE.match(sql)
    assert match is not None
    return match.group(1), match.group(2)


def _column_exists(cur: sqlite3.Cursor, table: str, column: str) -> bool:
    pragma_cur = cur.execute(f"PRAGMA table_info({table})")
    info_rows = cast("list[sqlite3.Row]", pragma_cur.fetchall())
    for row in info_rows:
        value_obj: object = row["name"]
        if isinstance(value_obj, str) and value_obj == column:
            return True
    return False


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
            CHECK(is_stale IN (0, 1)),
        chunk_id TEXT,
        span_id TEXT
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
    CREATE TABLE IF NOT EXISTS content_cache (
        content_hash TEXT PRIMARY KEY,
        language TEXT,
        extractor_version TEXT NOT NULL,
        extracted_at REAL NOT NULL,
        extraction_status TEXT NOT NULL,
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


@dataclass(frozen=True, slots=True)
class FileRow:
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
    chunk_id: str
    path: str
    start_line: int
    end_line: int
    text_hash: str
    role: str
    generation: int


@dataclass(frozen=True, slots=True)
class EvidenceRow:
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
    chunk_id: str | None = None
    span_id: str | None = None


@dataclass(frozen=True, slots=True)
class SpanRow:
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


@dataclass(frozen=True, slots=True)
class ContentCacheRow:
    content_hash: str
    language: str | None
    extractor_version: str
    extracted_at: float
    extraction_status: str
    error_summary: str | None


__all__ = [
    "ContentCacheChunk",
    "ContentCachePayload",
]


class Clock(Protocol):
    def now(self) -> float: ...


class SystemClock:
    def now(self) -> float:
        return time.monotonic()


def real_clock_seconds() -> float:
    return time.time()


def normalize_index_path(path: str) -> str:
    if not isinstance(path, str):
        raise ValueError(f"normalize_index_path: path must be a str, got {type(path).__name__}")
    from ralph.mcp.tools.workspace._utils import normalize_relative_path

    if path.startswith("/"):
        raise ValueError(f"normalize_index_path: absolute path {path!r} is not allowed")
    normalized = normalize_relative_path(path)
    if normalized != "" and (
        normalized == ".."
        or normalized.startswith("../")
        or "/../" in normalized
        or normalized.endswith("/..")
    ):
        raise ValueError(f"normalize_index_path: parent-escape path {path!r} is not allowed")
    return normalized


def _row_str(row: sqlite3.Row, key: str) -> str:
    value: object = row[key]
    if not isinstance(value, str):
        return str(value)
    return value


row_str = _row_str


def _row_int(row: sqlite3.Row, key: str) -> int:
    value: object = row[key]
    if isinstance(value, bool):
        return int(bool(value))
    if isinstance(value, int):
        return value
    return int(cast("int | str | float", value))


def _row_float(row: sqlite3.Row, key: str) -> float:
    value: object = row[key]
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    return float(cast("int | str | float", value))


def _row_bool(row: sqlite3.Row, key: str) -> bool:
    value: object = row[key]
    if isinstance(value, bool):
        return value
    return bool(value)


def _row_optional_str(row: sqlite3.Row, key: str) -> str | None:
    value: object = row[key]
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return str(value)


def _row_int_opt(row: sqlite3.Row | None, idx: int) -> int:
    if row is None:
        return 0
    value: object = row[idx]
    if isinstance(value, bool):
        return int(bool(value))
    if isinstance(value, int):
        return value
    return int(cast("int | str | float", value))


def _row_to_file(row: sqlite3.Row) -> FileRow:
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
        chunk_id=_row_optional_str(row, "chunk_id"),
        span_id=_row_optional_str(row, "span_id"),
    )


def _row_to_span(row: sqlite3.Row) -> SpanRow:
    return SpanRow(
        span_id=_row_str(row, "span_id"),
        path=_row_str(row, "path"),
        start_line=_row_int(row, "start_line"),
        start_col=_row_int(row, "start_col"),
        end_line=_row_int(row, "end_line"),
        end_col=_row_int(row, "end_col"),
        kind=_row_str(row, "kind"),
        symbol_id=_row_optional_str(row, "symbol_id"),
        content_hash=_row_str(row, "content_hash"),
        generation=_row_int(row, "generation"),
    )


def _row_to_symbol(row: sqlite3.Row) -> SymbolRow:
    return SymbolRow(
        symbol_id=_row_str(row, "symbol_id"),
        name=_row_str(row, "name"),
        qualified_name=_row_str(row, "qualified_name"),
        kind=_row_str(row, "kind"),
        path=_row_str(row, "path"),
        span_id=_row_str(row, "span_id"),
        language=_row_optional_str(row, "language"),
        extracted_from=_row_str(row, "extracted_from"),
        confidence=_row_float(row, "confidence"),
        generation=_row_int(row, "generation"),
    )


def _row_to_edge(row: sqlite3.Row) -> EdgeRow:
    return EdgeRow(
        edge_id=_row_str(row, "edge_id"),
        source_id=_row_str(row, "source_id"),
        target_id=_row_str(row, "target_id"),
        relation=_row_str(row, "relation"),
        path=_row_str(row, "path"),
        span_id=_row_optional_str(row, "span_id"),
        provenance=_row_str(row, "provenance"),
        confidence=_row_float(row, "confidence"),
        reason=_row_optional_str(row, "reason"),
        generation=_row_int(row, "generation"),
    )


def _row_to_content_cache(row: sqlite3.Row) -> ContentCacheRow:
    return ContentCacheRow(
        content_hash=_row_str(row, "content_hash"),
        language=_row_optional_str(row, "language"),
        extractor_version=_row_str(row, "extractor_version"),
        extracted_at=_row_float(row, "extracted_at"),
        extraction_status=_row_str(row, "extraction_status"),
        error_summary=_row_optional_str(row, "error_summary"),
    )


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def chunk_text(
    text: str, *, lines_per_chunk: int = DEFAULT_CHUNK_LINES
) -> list[tuple[int, int, str]]:
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
    payload = (
        f"ev\x00{path}\x00{content_hash}\x00{start_line}\x00{end_line}\x00"
        f"{kind}\x00{extractor_version}"
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


SKIP_DIR_NAMES: Final[frozenset[str]] = frozenset(
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
        ".hypothesis",
        "build",
        "_build",
        "dist",
    }
)


def iter_indexable_files(workspace_root: Path) -> Iterator[os.DirEntry[str]]:
    root = Path(workspace_root).resolve()
    stack: list[Path] = [root]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as it:
                entries = list(it)
        except OSError:
            continue
        subdirs: list[Path] = []
        for entry in entries:
            try:
                is_dir = entry.is_dir(follow_symlinks=False)
            except OSError:
                continue
            if is_dir:
                if entry.name in SKIP_DIR_NAMES:
                    continue
                subdirs.append(Path(entry.path))
                continue
            try:
                is_file = entry.is_file(follow_symlinks=False)
            except OSError:
                continue
            if is_file:
                yield entry
        stack.extend(reversed(subdirs))


def collect_workspace_files(workspace_root: Path) -> list[tuple[str, int, int]]:
    root = Path(workspace_root).resolve()
    rows: list[tuple[str, int, int]] = []
    for entry in iter_indexable_files(root):
        try:
            stat_result = entry.stat(follow_symlinks=False)
        except FileNotFoundError:
            continue
        relative = Path(entry.path).relative_to(root).as_posix()
        rows.append((relative, stat_result.st_size, stat_result.st_mtime_ns))
    return sorted(rows)


def hash_workspace_file(workspace_root: Path, relative_path: str) -> tuple[str, int, int]:
    root = Path(workspace_root).resolve()
    full = (root / relative_path).resolve()
    try:
        is_relative = full.is_relative_to(root)
    except AttributeError:
        is_relative = str(full).startswith(str(root) + os.sep) or full == root
    if not is_relative:
        raise ValueError(f"Path escapes workspace: {relative_path!r}")
    if not full.is_file():
        raise FileNotFoundError(relative_path)
    hasher = hashlib.sha256()
    with full.open("rb") as f:
        while True:
            chunk = f.read(1 << 20)
            if not chunk:
                break
            hasher.update(chunk)
    stat_result = full.stat()
    return (
        hasher.hexdigest(),
        stat_result.st_size,
        stat_result.st_mtime_ns,
    )


def assert_within_workspace(workspace_root: Path, full_path: Path) -> None:
    root = Path(workspace_root).resolve()
    resolved = Path(full_path).resolve()
    try:
        is_relative = resolved.is_relative_to(root)
    except AttributeError:
        is_relative = str(resolved).startswith(str(root) + os.sep) or resolved == root
    if not is_relative:
        raise ValueError(f"Path escapes workspace: {full_path}")


__all__ = [
    "DEFAULT_CHUNK_LINES",
    "DEFAULT_INDEX_DB",
    "DEFAULT_INDEX_ROOT",
    "ChunkRow",
    "Clock",
    "ContentCacheChunk",
    "ContentCachePayload",
    "ContentCacheRow",
    "EdgeRow",
    "EvidenceRow",
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
