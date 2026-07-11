"""Incremental reindex path for the indexed exploration substrate.

Extracted from :mod:`ralph.mcp.explore.pipeline` so the hub module
stays under the per-file line ceiling. This module owns the
warm-no-op / warm-small-edit / warm-changed paths, the bounded
manifest update, the per-path extraction, and the deadline-aware
worker loop. The staged full path lives in
:mod:`ralph.mcp.explore._pipeline_staged`.
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable, Sequence
from pathlib import Path

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
    ContentCacheChunk,
    ContentCachePayload,
    ContentCacheRow,
    EvidenceRow,
    ExploreStore,
    FileRow,
    chunk_text,
    collect_workspace_files,
    derive_chunk_id,
    derive_evidence_id,
    deserialize_content_cache_payload,
    serialize_content_cache_payload,
    sha256_text,
)
from ralph.mcp.explore.structure import (
    PythonExtractionError,
    extract_structure,
)

logger = logging.getLogger(__name__)


def _run_reindex(
    store: ExploreStore,
    workspace_root: Path,
    *,
    options: ReindexOptions,
    now_fn: Callable[[], float],
    state: _ReindexState,
    cancel: Callable[[], bool] | None = None,
) -> str:
    """Inner reindex logic. Returns a status string (without finalize)."""
    # AC-05: bounded cancel contract. Polled at the file-loop
    # boundary, at the FTS commit, and at the row-insert boundary
    # so cancellation latency is bounded by the duration of one
    # phase. The prior committed generation is preserved.
    def _check_cancel() -> None:
        if cancel is not None and cancel():
            raise _ReindexCancelledError("cancelled by caller")

    # ``_run_reindex`` runs in ``changed`` mode only. The public
    # ``reindex()`` entry point dispatches ``mode='full'`` to
    # ``_staged_full_reindex`` so the live store is never
    # partially modified. The change-replay path always
    # increments the generation so the prior committed state
    # is visibly distinct from the new one.
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

    for relative_path, (size_bytes, mtime_ns) in sorted(next_manifest.items()):
        _ensure_deadline(state, now_fn)
        _check_cancel()
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
        except PythonExtractionError as exc:
            # PA-001 / AC-02: the typed structure-extraction failure
            # is translated to the same per-path failure path as
            # any other FileReadError. Prior lexical/structure
            # rows are preserved (the preflight refused to write),
            # the path stays dirty, and the loop continues.
            state.failed_paths.append(relative_path)
            state.error_summary = f"extract_failed:python_syntax: {exc}"
            continue
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

    # Mark deleted paths. AC-05/AC-06: a removed file must drop its
    # chunks, FTS rows, evidence, spans, symbols, and edges so graph
    # queries and indexed text search cannot return facts about
    # files that no longer exist in the workspace. We tombstone the
    # path-level evidence before deletion (same contract as the
    # change-replacement path) and then physically delete the
    # per-path rows. The file row is recorded as is_deleted=1 for
    # diagnostics; a subsequent reindex that finds the file again
    # will replace the rows and clear the is_deleted flag.
    for existing in list(store.iter_files()):
        _ensure_deadline(state, now_fn)
        _check_cancel()
        if existing.path not in seen_paths and existing.path not in path_scope:
            # File is no longer on disk.
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
                stale_at=now_fn(),
                replacement_evidence_id=None,
            )
            store.delete_chunks_for_path(existing.path)
            # delete_file_rows removes files/chunks/chunks_fts/evidence
            # for the path; we still need to drop structure rows
            # (spans, symbols, edges) and re-mark the file row as
            # is_deleted for diagnostics.
            store.replace_structure_rows(
                path=existing.path,
                spans=(),
                symbols=(),
                edges=(),
            )
            store.delete_file_rows(existing.path)
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
    # AC-05/AC-06: persist the schema/extractor versions so a future
    # open can detect a mismatched persisted index and force a safe
    # cold rebuild. The keys are stable across reindex calls.
    from ralph.mcp.explore.store import SCHEMA_VERSION as _SCHEMA_VERSION
    from ralph.mcp.explore.structure import (
        EXTRACTOR_VERSION as _STRUCTURE_EXTRACTOR_VERSION,
    )

    store.set_setting("schema_version", _SCHEMA_VERSION)
    store.set_setting("extractor_version", EXTRACTOR_VERSION)
    store.set_setting("structure_extractor_version", _STRUCTURE_EXTRACTOR_VERSION)
    # AC-05 dirty-path safety: only consume dirty paths that were
    # actually processed (visited during this reindex pass) and that
    # are not in the failed set. Out-of-scope manual refreshes
    # consume their own paths so the queue does not grow over
    # repeated partial reindexes. Failed paths stay in the queue so
    # the next reindex retries them.
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
    """Return True when ``path`` falls within one of the ``path_scope`` roots.

    An empty ``path_scope`` is treated as "everything is in scope".
    """
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


def _dirty_paths_contain(store: ExploreStore, path: str) -> bool:
    """Return True when ``path`` is in the persisted dirty queue.

    Used by the reindex prefilter so a workspace mutation marked
    dirty by ``mark_path`` forces a re-hash even when the manifest
    size+mtime match the prior run.
    """
    normalized = path.replace(os.sep, "/")
    return any(existing == normalized for existing in store.peek_dirty_paths())


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

    PA-001 / AC-02: this function follows a strict preflight-then-
    replace ordering. Every per-path destructive store write
    (tombstone, chunk delete, structure delete, file upsert,
    chunk/FTS insert, evidence insert, structure upsert) happens
    only AFTER a successful read + decode + lexical chunk build +
    structure extraction preflight. If any preflight step raises
    (e.g. ``PythonSyntaxError`` for malformed Python), no
    destructive write fires, prior lexical/structure rows remain
    queryable, and the failure is reported to ``_run_reindex`` so
    the path lands in ``failed_files`` and stays dirty for a
    later retry.

    AC-05 content cache: when ``content_hash`` already has a
    fresh-enough cache row (matching ``EXTRACTOR_VERSION``), the
    lexical preflight reuses the cached chunks and skips the
    file read + chunk_text build. Structure rows (spans/symbols/
    edges) are still re-derived for the new path because their
    identifiers are path-dependent. The cache is then repopulated
    so a future copy/move of an equivalent file can reuse it
    again.

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

    # --- Content-cache lookup. AC-05: a cache hit skips the
    # ``chunk_text`` build because the cached payload already
    # carries every (start_line, end_line, text_hash) tuple.
    # File text is still read so ``extract_structure`` and any
    # structure rows can be re-derived for the new path
    # (path-derived ids and qualified_name depend on the path,
    # even when the file contents are identical). Saving only the
    # ``chunk_text`` + ``sha256_text`` work keeps the cache
    # path-neutral while still preserving structure correctness.
    cached_payload: ContentCachePayload | None = _maybe_load_cached_payload(
        store, content_hash
    )

    # --- Preflight phase: read + decode + build lexical + structure.
    # These calls happen BEFORE any destructive write so a failure
    # (malformed Python, OS read error, OOM) preserves every
    # prior row in the live store. The caller treats a raised
    # exception as a per-path failure recorded in failed_files;
    # the path stays dirty and is retried on the next pass.
    text: str = full.read_text(encoding="utf-8", errors="replace")
    prepared_chunks: list[tuple[int, int, str]]
    extracted_at: float
    if cached_payload is not None:
        # Cache hit: reuse the chunk coordinates but rebuild the
        # chunk rows with path-derived ids; this also keeps the
        # ``chunks_fts`` text fresh for the new path.
        prepared_chunks = [
            (chunk.start_line, chunk.end_line, chunk.text)
            for chunk in cached_payload.chunks
        ]
        extracted_at = time.time()
    else:
        prepared_chunks = list(
            chunk_text(text, lines_per_chunk=DEFAULT_CHUNK_LINES)
        )
        extracted_at = time.time()
    # ``extract_structure`` raises ``PythonSyntaxError`` for
    # malformed Python; the typed exception propagates so the
    # caller fails-closed without losing prior rows. Structure is
    # always rederived because symbol/span/edge ids are path-bound.
    prepared_extraction = extract_structure(
        path=relative_path,
        content=text,
        content_hash=content_hash,
        generation=generation,
    )

    # --- Replacement phase: tombstone + delete + upsert only after
    # the preflight succeeds.
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
    # Phase 2 (AC-06): persist deterministic structure rows for
    # Python and Markdown files. Unsupported languages keep their
    # lexical rows only — no graph edges are emitted for them.
    if (
        prepared_extraction.spans
        or prepared_extraction.symbols
        or prepared_extraction.edges
    ):
        store.replace_structure_rows(
            path=relative_path,
            spans=prepared_extraction.spans,
            symbols=prepared_extraction.symbols,
            edges=prepared_extraction.edges,
        )

    # AC-05: repopulate the content cache when we performed fresh
    # lexical work. Cache hits do not need to rewrite the cache
    # because the row was already written by the prior extraction
    # that populated it. The repopulation step is best-effort: if
    # the cache write raises, we log and continue because the
    # chunk/evidence rows are already persisted.
    if cached_payload is None:
        try:
            _write_cached_payload(
                store,
                content_hash=content_hash,
                language=_detect_language(relative_path),
                prepared_chunks=prepared_chunks,
                extracted_at=extracted_at,
            )
        except Exception:  # bounded: cache writes never block reindex
            logger.warning("content_cache write failed for %s", relative_path)


def _maybe_load_cached_payload(
    store: ExploreStore,
    content_hash: str,
) -> ContentCachePayload | None:
    """Return a deserialized cache payload or ``None`` on miss/invalid.

    AC-05: a cache hit only counts when ``EXTRACTOR_VERSION`` is
    current. Stale-version hits return ``None`` so the pipeline
    re-extracts instead of trusting an obsolete schema. Malformed
    payloads are dropped and treated as cache misses so a corrupt
    row cannot poison subsequent reindexes.
    """
    row: ContentCacheRow | None = store.lookup_content_cache(
        content_hash=content_hash,
        extractor_version=EXTRACTOR_VERSION,
    )
    if row is None:
        return None
    blob = store.read_content_cache_payload(content_hash=content_hash)
    if blob is None:
        return None
    try:
        return deserialize_content_cache_payload(blob)
    except ValueError:
        return None


def _write_cached_payload(
    store: ExploreStore,
    *,
    content_hash: str,
    language: str | None,
    prepared_chunks: list[tuple[int, int, str]],
    extracted_at: float,
) -> None:
    """Insert or refresh the content cache for ``content_hash``.

    Builds a fresh :class:`ContentCachePayload` from the freshly
    chunked text and serializes it into a deterministic JSON BLOB.
    The chunk ``text_hash`` is the same ``sha256_text`` the
    pipeline computes for the ``chunks`` table, so the cache row
    is interchangeable with the live ``chunks`` rows without an
    extra hash recompute.
    """
    cache_chunks: list[ContentCacheChunk] = []
    for start_line, end_line, body in prepared_chunks:
        cache_chunks.append(
            ContentCacheChunk(
                start_line=start_line,
                end_line=end_line,
                text_hash=sha256_text(body),
                text=body,
                role="body",
            )
        )
    payload = ContentCachePayload(
        content_hash=content_hash,
        extractor_version=EXTRACTOR_VERSION,
        chunks=tuple(cache_chunks),
    )
    blob = serialize_content_cache_payload(payload)
    store.insert_content_cache(
        row=ContentCacheRow(
            content_hash=content_hash,
            language=language,
            extractor_version=EXTRACTOR_VERSION,
            extracted_at=extracted_at,
            extraction_status="ok",
            error_summary=None,
        ),
        payload=blob,
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
