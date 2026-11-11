
from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Final

from ralph.mcp.explore import pipeline as _pipeline_module
from ralph.mcp.explore._pipeline_state import (
    EXTRACTOR_VERSION,
    FileReadError,
    ReindexOptions,
    _ReindexCancelledError,
    _ReindexState,
    _ReindexTimeoutError,
)
from ralph.mcp.explore.store import (
    DEFAULT_CHUNK_LINES,
    ChunkRow,
    ContentCacheRow,
    EvidenceRow,
    ExploreStore,
    FileRow,
    chunk_text,
    collect_workspace_files,
    derive_chunk_id,
    derive_evidence_id,
    sha256_text,
)
from ralph.mcp.explore.structure import (
    PythonExtractionError,
    extract_structure,
)

logger = logging.getLogger(__name__)

_BATCH_SIZE: Final[int] = 50


def _run_reindex(
    store: ExploreStore,
    workspace_root: Path,
    *,
    options: ReindexOptions,
    now_fn: Callable[[], float],
    state: _ReindexState,
    cancel: Callable[[], bool] | None = None,
) -> str:

    def _check_cancel() -> None:
        if cancel is not None and cancel():
            raise _ReindexCancelledError("cancelled by caller")

    current_generation = _current_generation(store)
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

    dirty_set: frozenset[str] = frozenset(dirty_paths)

    manifest_items = sorted(next_manifest.items())
    for batch_start in range(0, len(manifest_items), _BATCH_SIZE):
        batch = manifest_items[batch_start : batch_start + _BATCH_SIZE]
        batch_paths = [relative_path for relative_path, _ in batch]
        previous_by_path = store.get_file_many(batch_paths)

        no_op_paths: list[str] = []
        no_op_hashes: list[str] = []
        no_op_sizes: list[int] = []
        no_op_mtimes: list[int] = []

        with store.transaction():
            for relative_path, (size_bytes, mtime_ns) in batch:
                _ensure_deadline(state, now_fn)
                _check_cancel()
                seen_paths.add(relative_path)
                previous = previous_by_path.get(relative_path)
                if (
                    previous is not None
                    and not previous.is_deleted
                    and previous.size_bytes == size_bytes
                    and previous.mtime_ns == mtime_ns
                    and relative_path not in dirty_set
                ):
                    no_op_paths.append(relative_path)
                    no_op_hashes.append(previous.content_hash)
                    no_op_sizes.append(size_bytes)
                    no_op_mtimes.append(mtime_ns)
                    continue

                try:
                    content_hash, actual_size, actual_mtime = _pipeline_module.hash_workspace_file(
                        workspace_root, relative_path
                    )
                except (FileNotFoundError, ValueError):
                    state.failed_paths.append(relative_path)
                    continue
                except OSError as exc:
                    state.failed_paths.append(relative_path)
                    state.error_summary = f"hash_failed: {exc}"
                    continue

                if (
                    previous is not None
                    and previous.content_hash == content_hash
                    and not previous.is_deleted
                ):
                    _update_manifest(
                        store,
                        relative_path,
                        content_hash=content_hash,
                        size_bytes=actual_size,
                        mtime_ns=actual_mtime,
                        last_seen_generation=target_generation,
                    )
                    continue

                try:
                    _re_extract_path(
                        store,
                        workspace_root=workspace_root,
                        relative_path=relative_path,
                        content_hash=content_hash,
                        size_bytes=actual_size,
                        mtime_ns=actual_mtime,
                        generation=target_generation,
                        structure_extractor=options.structure_extractor,
                    )
                except PythonExtractionError as exc:
                    state.failed_paths.append(relative_path)
                    state.error_summary = f"extract_failed:python_syntax: {exc}"
                    continue
                except FileReadError as exc:
                    state.failed_paths.append(relative_path)
                    state.error_summary = f"extract_failed: {exc}"
                    _update_manifest(
                        store,
                        relative_path,
                        content_hash=content_hash,
                        size_bytes=actual_size,
                        mtime_ns=actual_mtime,
                        last_seen_generation=target_generation,
                    )
                    store.upsert_file(
                        FileRow(
                            path=relative_path,
                            content_hash=content_hash,
                            size_bytes=actual_size,
                            mtime_ns=actual_mtime,
                            language=None,
                            indexed_generation=target_generation,
                            indexed_at=time.time(),
                            is_deleted=False,
                        )
                    )
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

            if no_op_paths:
                store.upsert_manifest_many(
                    no_op_paths,
                    no_op_hashes,
                    no_op_sizes,
                    no_op_mtimes,
                    target_generation,
                )

    existing_files = list(store.iter_files())
    for batch_start in range(0, len(existing_files), _BATCH_SIZE):
        batch_existing = existing_files[batch_start : batch_start + _BATCH_SIZE]
        with store.transaction():
            for existing in batch_existing:
                _ensure_deadline(state, now_fn)
                _check_cancel()
                if existing.path not in seen_paths and existing.path not in path_scope:
                    store.record_tombstone(
                        evidence_id=derive_evidence_id(
                            path=existing.path,
                            content_hash=existing.content_hash,
                            start_line=0,
                            end_line=0,
                            kind="path_tombstone",
                            extractor_version=EXTRACTOR_VERSION,
                        ),
                        path=existing.path,
                        start_line=0,
                        end_line=0,
                        content_hash=existing.content_hash,
                        generation=existing.indexed_generation,
                        stale_reason="file_deleted",
                        stale_at=time.time(),
                        replacement_evidence_id=None,
                    )
                    store.delete_chunks_for_path(existing.path)
                    store.replace_structure_rows(
                        path=existing.path,
                        spans=(),
                        symbols=(),
                        edges=(),
                    )
                    store.delete_file_rows(existing.path)
    with store.transaction():
        store._conn.execute("DELETE FROM files WHERE is_deleted = 1")

    store.set_setting("current_generation", str(target_generation))
    from ralph.mcp.explore.store import SCHEMA_VERSION as _SCHEMA_VERSION
    from ralph.mcp.explore.structure import (
        EXTRACTOR_VERSION as _STRUCTURE_EXTRACTOR_VERSION,
    )

    store.set_setting("schema_version", _SCHEMA_VERSION)
    store.set_setting("extractor_version", EXTRACTOR_VERSION)
    store.set_setting("structure_extractor_version", _STRUCTURE_EXTRACTOR_VERSION)
    out_of_scope: set[str] = set()
    if path_scope:
        dirty = store.peek_dirty_paths()
        for p in dirty:
            if not _path_in_scope(p, list(path_scope)):
                out_of_scope.add(p)
    failed_set = set(state.failed_paths)
    survivors = (seen_paths | out_of_scope) - failed_set
    for p in sorted(survivors):
        store._remove_dirty_path(p)
    if state.parse_count == 0 and not state.failed_paths:
        return "skipped_no_changes"
    return "ok"


def _ensure_deadline(state: _ReindexState, now_fn: Callable[[], float]) -> None:
    if (now_fn() - state.started_at) * 1000 > state.deadline_ms:
        state.timed_out = True
        raise _ReindexTimeoutError("deadline exceeded")


def _path_in_scope(path: str, path_scope: Sequence[str]) -> bool:
    if not path_scope:
        return True
    for raw_scope in path_scope:
        normalized_scope = raw_scope.rstrip("/")
        if not normalized_scope:
            return True
        if path == normalized_scope or path.startswith(normalized_scope + "/"):
            return True
    return False


def _current_generation(store: ExploreStore) -> int:
    raw = store.get_setting("current_generation")
    if raw is None or not raw.isdigit():
        return 0
    return int(raw)


def _update_manifest(
    store: ExploreStore,
    path: str,
    *,
    content_hash: str,
    size_bytes: int,
    mtime_ns: int,
    last_seen_generation: int,
) -> None:
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
    structure_extractor: str = "scalar",
) -> None:
    full = (Path(workspace_root) / relative_path).resolve()
    root = Path(workspace_root).resolve()
    try:
        is_relative = full.is_relative_to(root)
    except AttributeError:
        is_relative = str(full).startswith(str(root) + os.sep) or full == root
    if not is_relative:
        raise FileReadError(f"path escapes workspace: {relative_path!r}")

    raw_bytes = full.read_bytes()
    try:
        text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise FileReadError(
            f"invalid utf-8 encoding: {relative_path!r} at byte {exc.start}"
        ) from exc
    prepared_chunks: list[tuple[int, int, str]] = list(
        chunk_text(text, lines_per_chunk=DEFAULT_CHUNK_LINES)
    )
    extracted_at: float = time.time()
    prepared_extraction = extract_structure(
        path=relative_path,
        content=text,
        content_hash=content_hash,
        generation=generation,
        structure_extractor=structure_extractor,
    )

    previous = store.get_file(relative_path)
    if previous is not None:
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
    for start_line, end_line, body in prepared_chunks:
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
    if prepared_extraction.spans or prepared_extraction.symbols or prepared_extraction.edges:
        store.replace_structure_rows(
            path=relative_path,
            spans=prepared_extraction.spans,
            symbols=prepared_extraction.symbols,
            edges=prepared_extraction.edges,
        )

    try:
        store.insert_content_cache(
            row=ContentCacheRow(
                content_hash=content_hash,
                language=_detect_language(relative_path),
                extractor_version=EXTRACTOR_VERSION,
                extracted_at=extracted_at,
                extraction_status="ok",
                error_summary=None,
            )
        )
    except Exception:
        logger.warning("content_cache write failed for %s", relative_path)


def _detect_language(path: str) -> str | None:
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
