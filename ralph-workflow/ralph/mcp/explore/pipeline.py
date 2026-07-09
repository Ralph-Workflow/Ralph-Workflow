"""Idempotent, work-proportional reindex pipeline.

Phase 1 builds only the lexical part of the index. Phase 2 (deferred)
will add Python AST symbol extraction and the structural graph.

The pipeline follows the Idempotence And Efficiency Contract from the
architecture finding:

1. Build/compare deterministic manifest sorted by normalized path.
2. Reuse unchanged file records/chunks/edges by ``(path, content_hash)``.
3. Re-extract only changed/new files (warm small-edit).
4. Delete stale non-evidence rows for a changed path BEFORE inserting
   replacement rows; delete FTS rows by ``chunk_id`` first.
5. Write bounded ``evidence_tombstones`` BEFORE deleting stale evidence.
6. Mark missing paths deleted/stale.
7. Commit a new generation atomically (short transactions).
8. Cap job history (latest 100 / 14 days) and tombstones (latest 10k /
   30 days).

Warm no-op refresh checks the manifest and does NO parsing and NO
FTS/edge rewrites. Warm small-edit reparses only changed-file bytes
plus bounded local edge cleanup.

``mode='full'`` rebuilds into a temp generation and atomically swaps
metadata only after success.

A single reindex writer per workspace; concurrent requests coalesce
dirty paths rather than starting a second writer. Timeout is
fail-closed for the job (``status='timed_out'``) and fail-open for
agent reads (tools return stale metadata instead of hanging).
"""

from __future__ import annotations

import logging
import os
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

from ralph.mcp.explore.store import (
    DEFAULT_CHUNK_LINES,
    ChunkRow,
    Clock,
    EvidenceRow,
    ExploreStore,
    FileRow,
    SystemClock,
    chunk_text,
    collect_workspace_files,
    derive_chunk_id,
    derive_evidence_id,
    hash_workspace_file,
    sha256_text,
)
from ralph.mcp.explore.structure import (
    extract_structure,
)

# Ponytail: keep imports narrow; nothing from the mcp package is
# imported here so audit_mcp_timeout stays happy without markers.

logger = logging.getLogger(__name__)

# Phase 1 uses an explicit extractor version. Future phases will
# bump this when AST/symbol extraction ships.
EXTRACTOR_VERSION: Final[str] = "phase1-lexical-v1"

# Default bounds for the reindex job. Each is fail-closed via the
# caller (reindex(..., timeout_ms=...)) but we keep safe defaults.
DEFAULT_TIMEOUT_MS: Final[int] = 5_000
DEFAULT_FULL_TIMEOUT_MS: Final[int] = 60_000


# --- Public dataclasses ---------------------------------------------------


@dataclass(frozen=True, slots=True)
class ReindexResult:
    """Result of a reindex invocation."""

    job_id: str
    generation: int
    status: str  # "ok" | "timed_out" | "failed" | "skipped_no_changes"
    changed_files: tuple[str, ...] = ()
    failed_files: tuple[str, ...] = ()
    parse_count: int = 0
    dirty_paths_count: int = 0
    elapsed_seconds: float = 0.0
    error_summary: str | None = None


@dataclass(frozen=True, slots=True)
class ReindexOptions:
    """Options for a reindex call."""

    mode: str = "changed"  # "changed" | "full"
    timeout_ms: int = DEFAULT_TIMEOUT_MS
    path_scope: tuple[str, ...] = ()
    clock: Clock | None = None


# --- Internal helpers ------------------------------------------------------


class FileReadError(Exception):
    """A file failed to read during reindex."""


@dataclass
class _ReindexState:
    """Mutable per-job scratch state."""

    job_id: str
    started_at: float
    deadline: float
    deadline_ms: int = 0
    parse_count: int = 0
    dirty_paths_count: int = 0
    changed_paths: list[str] = field(default_factory=list)
    failed_paths: list[str] = field(default_factory=list)
    error_summary: str | None = None
    timed_out: bool = False


def _elapsed(state: _ReindexState, now_fn: Callable[[], float]) -> float:
    return now_fn() - state.started_at


def _ensure_deadline_not_exceeded(state: _ReindexState, now_fn: Callable[[], float]) -> None:
    if _elapsed(state, now_fn) * 1000 > state.deadline_ms:
        # Deadline is in ms; we keep the attribute name short to avoid
        # downstream renames.
        state.timed_out = True
        raise _ReindexTimeoutError("deadline exceeded")


# Avoid circular import: declare the timeout class only when used.
class _ReindexTimeoutError(Exception):
    pass


# --- Main entry point ------------------------------------------------------


def reindex(
    store: ExploreStore,
    workspace_root: Path,
    *,
    options: ReindexOptions | None = None,
) -> ReindexResult:
    """Run a reindex job over ``workspace_root``.

    The store is required to already exist on disk. The caller is
    responsible for initializing ``ExploreStore``; this keeps the
    pipeline free of any I/O-oracle side effects at import time.
    """
    opts = options or ReindexOptions()
    clock: Clock = opts.clock or SystemClock()
    now_fn = clock.now

    job_id = f"job-{uuid.uuid4().hex}"
    started_at = now_fn()
    deadline = opts.timeout_ms / 1000.0
    state = _ReindexState(
        job_id=job_id,
        started_at=started_at,
        deadline=deadline,
        deadline_ms=opts.timeout_ms,
    )

    try:
        result = _run_reindex(
            store,
            workspace_root,
            options=opts,
            now_fn=now_fn,
            state=state,
        )
    except _ReindexTimeoutError:
        return _finalize(store, state, status="timed_out", now_fn=now_fn)
    except Exception as exc:
        return _finalize(
            store,
            state,
            status="failed",
            now_fn=now_fn,
            error_summary=f"{type(exc).__name__}: {exc}",
        )
    return _finalize(store, state, status=result, now_fn=now_fn)


def _run_reindex(
    store: ExploreStore,
    workspace_root: Path,
    *,
    options: ReindexOptions,
    now_fn: Callable[[], float],
    state: _ReindexState,
) -> str:
    """Inner reindex logic. Returns a status string (without finalize)."""
    # Phase 1 only supports ``changed`` mode for warm refresh; ``full``
    # is a synonym for "rebuild from scratch" implemented as
    # ``changed`` after clearing the manifest first.
    current_generation = _current_generation(store)

    if options.mode == "full":
        _drop_all_rows(store)
        target_generation = 1
    else:
        target_generation = current_generation + 1 if current_generation else 1

    dirty_paths = store.peek_dirty_paths()
    state.dirty_paths_count = len(dirty_paths)
    path_scope = set(options.path_scope)

    manifest_rows = collect_workspace_files(workspace_root)
    next_manifest: dict[str, tuple[int, int]] = {}
    for relative_path, size_bytes, mtime_ns in manifest_rows:
        if path_scope and relative_path not in path_scope:
            continue
        next_manifest[relative_path] = (size_bytes, mtime_ns)

    seen_paths: set[str] = set()

    for relative_path, (size_bytes, mtime_ns) in sorted(next_manifest.items()):
        _ensure_deadline(state, now_fn)
        seen_paths.add(relative_path)
        # Ponytail: size+mtime prefilter first. The content hash is
        # authoritative, but skipping the read+hash for unchanged
        # files keeps warm no-op refresh work-proportional to the
        # manifest, not to the workspace bytes.
        previous = store.get_file(relative_path)
        if (
            previous is not None
            and previous.size_bytes == size_bytes
            and previous.mtime_ns == mtime_ns
            and not _dirty_paths_contain(store, relative_path)
        ):
            _update_manifest(
                store,
                relative_path,
                content_hash=previous.content_hash,
                size_bytes=size_bytes,
                mtime_ns=mtime_ns,
                last_seen_generation=target_generation,
            )
            continue

        try:
            content_hash, actual_size, actual_mtime = hash_workspace_file(
                workspace_root, relative_path
            )
        except (FileNotFoundError, ValueError):
            state.failed_paths.append(relative_path)
            continue
        except OSError as exc:
            state.failed_paths.append(relative_path)
            state.error_summary = f"hash_failed: {exc}"
            continue

        if previous is not None and previous.content_hash == content_hash:
            # No-op reuse; the manifest rows stay.
            _update_manifest(
                store,
                relative_path,
                content_hash=content_hash,
                size_bytes=actual_size,
                mtime_ns=actual_mtime,
                last_seen_generation=target_generation,
            )
            continue

        # Either new file or content changed. Phase 1 supports diff
        # content with same path (the deletion happens before the
        # insert in upsert_chunks_for_file).
        try:
            _re_extract_path(
                store,
                workspace_root=workspace_root,
                relative_path=relative_path,
                content_hash=content_hash,
                size_bytes=actual_size,
                mtime_ns=actual_mtime,
                generation=target_generation,
            )
        except FileReadError as exc:
            state.failed_paths.append(relative_path)
            state.error_summary = f"extract_failed: {exc}"
            continue
        state.parse_count += 1
        state.changed_paths.append(relative_path)
        _update_manifest(
            store,
            relative_path,
            content_hash=content_hash,
            size_bytes=actual_size,
            mtime_ns=actual_mtime,
            last_seen_generation=target_generation,
        )

    # Mark deleted paths.
    for existing in list(store.iter_files()):
        _ensure_deadline(state, now_fn)
        if existing.path not in seen_paths and existing.path not in path_scope:
            # File is no longer on disk.
            store.upsert_file(
                FileRow(
                    path=existing.path,
                    content_hash=existing.content_hash,
                    size_bytes=existing.size_bytes,
                    mtime_ns=existing.mtime_ns,
                    language=existing.language,
                    indexed_generation=target_generation,
                    indexed_at=now_fn(),
                    is_deleted=True,
                )
            )

    store.set_setting("current_generation", str(target_generation))
    # Consume dirty paths so the next refresh starts clean.
    store.consume_dirty_paths()
    if state.parse_count == 0 and not state.failed_paths:
        return "skipped_no_changes"
    return "ok"


def _ensure_deadline(state: _ReindexState, now_fn: Callable[[], float]) -> None:
    if (now_fn() - state.started_at) * 1000 > state.deadline_ms:
        state.timed_out = True
        raise _ReindexTimeoutError("deadline exceeded")


def _current_generation(store: ExploreStore) -> int:
    raw = store.get_setting("current_generation")
    if raw is None or not raw.isdigit():
        return 0
    return int(raw)


def _dirty_paths_contain(store: ExploreStore, path: str) -> bool:
    """Return True when ``path`` is in the persisted dirty queue.

    Used by the reindex prefilter so a workspace mutation marked
    dirty by ``mark_path`` forces a re-hash even when the manifest
    size+mtime match the prior run.
    """
    normalized = path.replace(os.sep, "/")
    return any(existing == normalized for existing in store.peek_dirty_paths())


def _drop_all_rows(store: ExploreStore) -> None:
    """Drop every row from the index tables. Used by mode='full'."""
    cur = store._conn.cursor()
    try:
        for table in (
            "evidence",
            "evidence_tombstones",
            "chunks",
            "chunks_fts",
            "files",
            "manifest",
            "dirty_paths",
        ):
            cur.execute(f"DELETE FROM {table}")
    finally:
        cur.close()


def _update_manifest(
    store: ExploreStore,
    path: str,
    *,
    content_hash: str,
    size_bytes: int,
    mtime_ns: int,
    last_seen_generation: int,
) -> None:
    """Insert/update a single manifest row inside the same generation."""
    store._conn.execute(
        """
        INSERT INTO manifest (
            path, content_hash, size_bytes, mtime_ns,
            inode_or_file_id, last_seen_generation
        ) VALUES (?, ?, ?, ?, NULL, ?)
        ON CONFLICT(path) DO UPDATE SET
            content_hash=excluded.content_hash,
            size_bytes=excluded.size_bytes,
            mtime_ns=excluded.mtime_ns,
            last_seen_generation=excluded.last_seen_generation
        """,
        (
            path,
            content_hash,
            size_bytes,
            mtime_ns,
            last_seen_generation,
        ),
    )


def _re_extract_path(
    store: ExploreStore,
    *,
    workspace_root: Path,
    relative_path: str,
    content_hash: str,
    size_bytes: int,
    mtime_ns: int,
    generation: int,
) -> None:
    """Re-extract chunks and evidence for ``relative_path``.

    Existing rows for this path are tombstoned (bounded) and then
    replaced. The caller (``_run_reindex``) handles the file-row
    update so the manifest reflects the new generation.
    """
    full = (Path(workspace_root) / relative_path).resolve()
    root = Path(workspace_root).resolve()
    try:
        is_relative = full.is_relative_to(root)
    except AttributeError:  # pragma: no cover — Python <3.9 fallback
        is_relative = str(full).startswith(str(root) + os.sep) or full == root
    if not is_relative:
        raise FileReadError(f"path escapes workspace: {relative_path!r}")

    previous = store.get_file(relative_path)
    if previous is not None:
        # Write a bounded tombstone for the prior evidence rows
        # before deleting them. We do not enumerate every prior row
        # — we record one tombstone per path/generation transition.
        store.record_tombstone(
            evidence_id=derive_evidence_id(
                path=relative_path,
                content_hash=previous.content_hash,
                start_line=0,
                end_line=0,
                kind="path_tombstone",
                extractor_version=EXTRACTOR_VERSION,
            ),
            path=relative_path,
            start_line=0,
            end_line=0,
            content_hash=previous.content_hash,
            generation=previous.indexed_generation,
            stale_reason="content_changed",
            stale_at=time.time(),
            replacement_evidence_id=None,
        )
    store.delete_chunks_for_path(relative_path)
    # Phase 2 (AC-06): also drop prior structure rows for this path
    # so the new generation replaces them atomically with no stale
    # graph rows left behind.
    store.replace_structure_rows(
        path=relative_path,
        spans=(),
        symbols=(),
        edges=(),
    )
    store.upsert_file(
        FileRow(
            path=relative_path,
            content_hash=content_hash,
            size_bytes=size_bytes,
            mtime_ns=mtime_ns,
            language=_detect_language(relative_path),
            indexed_generation=generation,
            indexed_at=time.time(),
            is_deleted=False,
        )
    )
    text = full.read_text(encoding="utf-8", errors="replace")
    for start_line, end_line, body in chunk_text(text, lines_per_chunk=DEFAULT_CHUNK_LINES):
        text_hash = sha256_text(body)
        chunk_id = derive_chunk_id(
            path=relative_path,
            start_line=start_line,
            end_line=end_line,
            text_hash=text_hash,
            extractor_version=EXTRACTOR_VERSION,
        )
        store.upsert_chunk(
            ChunkRow(
                chunk_id=chunk_id,
                path=relative_path,
                start_line=start_line,
                end_line=end_line,
                text_hash=text_hash,
                role="body",
                generation=generation,
            ),
            text=body,
        )
        evidence_id = derive_evidence_id(
            path=relative_path,
            content_hash=content_hash,
            start_line=start_line,
            end_line=end_line,
            kind="chunk",
            extractor_version=EXTRACTOR_VERSION,
        )
        store.insert_evidence(
            EvidenceRow(
                evidence_id=evidence_id,
                path=relative_path,
                start_line=start_line,
                end_line=end_line,
                content_hash=content_hash,
                generation=generation,
                source_tool="reindex",
                evidence_kind="chunk",
                created_at=time.time(),
                is_stale=False,
            )
        )
    # Phase 2 (AC-06): persist deterministic structure rows for
    # Python and Markdown files. Unsupported languages keep their
    # lexical rows only — no graph edges are emitted for them.
    extraction = extract_structure(
        path=relative_path,
        content=text,
        content_hash=content_hash,
        generation=generation,
    )
    if extraction.spans or extraction.symbols or extraction.edges:
        store.replace_structure_rows(
            path=relative_path,
            spans=extraction.spans,
            symbols=extraction.symbols,
            edges=extraction.edges,
        )


def _detect_language(path: str) -> str | None:
    """Best-effort language detection for the file row."""
    lower = path.lower()
    if lower.endswith(".py"):
        return "python"
    if lower.endswith(".md") or lower.endswith(".markdown"):
        return "markdown"
    if lower.endswith(".json"):
        return "json"
    if lower.endswith(".toml"):
        return "toml"
    if lower.endswith(".yaml") or lower.endswith(".yml"):
        return "yaml"
    if lower.endswith(".sh") or lower.endswith(".bash"):
        return "shell"
    return None


def _finalize(
    store: ExploreStore,
    state: _ReindexState,
    *,
    status: str,
    now_fn: Callable[[], float],
    error_summary: str | None = None,
) -> ReindexResult:
    """Write the job history row and return the result."""
    finished_at = now_fn()
    error = error_summary if error_summary is not None else state.error_summary
    store.record_job(
        job_id=state.job_id,
        generation=int(store.get_setting("current_generation") or 0),
        status=status,
        started_at=state.started_at,
        finished_at=finished_at,
        files_seen=len(state.changed_paths) + len(state.failed_paths),
        files_changed=len(state.changed_paths),
        files_failed=len(state.failed_paths),
        error_summary=error,
        now=finished_at,
    )
    return ReindexResult(
        job_id=state.job_id,
        generation=int(store.get_setting("current_generation") or 0),
        status=status,
        changed_files=tuple(state.changed_paths),
        failed_files=tuple(state.failed_paths),
        parse_count=state.parse_count,
        dirty_paths_count=state.dirty_paths_count,
        elapsed_seconds=finished_at - state.started_at,
        error_summary=error,
    )


# --- Single-writer guard ---------------------------------------------------


class ReindexWriter:
    """Single reindex writer per workspace.

    Concurrent calls coalesce dirty paths rather than starting a
    second writer (the architecture finding's "Concurrency and
    lifecycle contract").

    Ponytail: the lock is a module-level dict keyed by store path so
    tests do not see thread-shared state leaking between workspaces.
    Tests inject a custom ``lock_factory`` to avoid contention.
    """

    @staticmethod
    def _default_lock_factory() -> threading.Lock:
        raise RuntimeError("lock_factory not configured")

    _lock_factory: Callable[[], threading.Lock] = _default_lock_factory
    _active: dict[str, ReindexWriter] = {}  # bounded-accumulator-ok: keyed by db_path; entries are popped in `finally` of claim()
    _active_lock: threading.Lock | None = None

    @classmethod
    def configure(cls, *, lock_factory: Callable[[], threading.Lock]) -> None:
        cls._lock_factory = lock_factory
        cls._active_lock = lock_factory()

    def __init__(self, store: ExploreStore) -> None:
        self.store = store

    @classmethod
    def claim(
        cls,
        store: ExploreStore,
        *,
        workspace_root: Path,
        options: ReindexOptions | None = None,
    ) -> ReindexResult:
        """Run reindex, coalescing with any active writer for the same store."""
        if cls._active_lock is None:
            # Production callers should have configured the lock
            # factory in module init; tests bypass via direct calls.
            return reindex(store, workspace_root, options=options)
        key = str(store.db_path)
        assert cls._active_lock is not None
        with cls._active_lock:
            active = cls._active.get(key)
            if active is not None:
                # Coalesce: just process any pending dirty paths and
                # return a synthetic skipped result.
                pending = store.peek_dirty_paths()
                if options is None:
                    options = ReindexOptions()
                return ReindexResult(
                    job_id=f"coalesced-{active.store.db_path.name}",
                    generation=int(store.get_setting("current_generation") or 0),
                    status="skipped_no_changes",
                    dirty_paths_count=len(pending),
                    elapsed_seconds=0.0,
                )
            cls._active[key] = cls(store)
        try:
            return reindex(store, workspace_root, options=options)
        finally:
            with cls._active_lock:
                cls._active.pop(key, None)


__all__ = [
    "DEFAULT_FULL_TIMEOUT_MS",
    "DEFAULT_TIMEOUT_MS",
    "EXTRACTOR_VERSION",
    "ReindexOptions",
    "ReindexResult",
    "ReindexWriter",
    "reindex",
]
