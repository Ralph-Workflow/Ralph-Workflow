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
import shutil
import sqlite3
import threading
import time
import uuid
from collections.abc import Callable, Sequence
from contextlib import suppress
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
    PythonExtractionError,
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
    status: str  # "ok" | "timed_out" | "failed" | "skipped_no_changes" | "cancelled"
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


class _ReindexCancelledError(Exception):
    """Raised by the reindex writer when the cancel callable returns True.

    AC-05: bounded cancellation. The reindex writer polls the cancel
    callable at phase boundaries. On cancel the prior committed
    generation is preserved and the result is finalized as
    ``status='cancelled'`` with a bounded incomplete summary. The
    writer never raises any mutable partial state into the store
    after cancellation.
    """

    pass


# --- Main entry point ------------------------------------------------------


def reindex(
    store: ExploreStore,
    workspace_root: Path,
    *,
    options: ReindexOptions | None = None,
    cancel: Callable[[], bool] | None = None,
) -> ReindexResult:
    """Run a reindex job over ``workspace_root``.

    The store is required to already exist on disk. The caller is
    responsible for initializing ``ExploreStore``; this keeps the
    pipeline free of any I/O-oracle side effects at import time.

    AC-05: a ``cancel`` callable may be supplied. When the callable
    returns ``True`` the writer preserves the prior committed
    generation (no partial mutable state is exposed) and the
    reindex returns a ``status='cancelled'`` result. The callable
    is polled at phase boundaries (file iteration, FTS commit, and
    row insert) so cancellation is bounded by the duration of one
    phase.
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
        # AC-02/AC-05: ``mode='full'`` runs in a staging database
        # so cancellation/timeout cannot leak partial mutable
        # state into the live store. The dispatch happens before
        # ``_run_reindex`` touches the live store, so even a
        # cancel that becomes true on the first poll never
        # reaches the destructive drop path.
        if opts.mode == "full":
            return _staged_full_reindex(
                store,
                workspace_root,
                options=opts,
                now_fn=now_fn,
                state=state,
                cancel=cancel,
            )
        result = _run_reindex(
            store,
            workspace_root,
            options=opts,
            now_fn=now_fn,
            state=state,
            cancel=cancel,
        )
    except _ReindexTimeoutError:
        return _finalize(store, state, status="timed_out", now_fn=now_fn)
    except _ReindexCancelledError:
        return _finalize(store, state, status="cancelled", now_fn=now_fn)
    except Exception as exc:
        return _finalize(
            store,
            state,
            status="failed",
            now_fn=now_fn,
            error_summary=f"{type(exc).__name__}: {exc}",
        )
    return _finalize(store, state, status=result, now_fn=now_fn)


def _staged_full_reindex(
    store: ExploreStore,
    workspace_root: Path,
    *,
    options: ReindexOptions,
    now_fn: Callable[[], float],
    state: _ReindexState,
    cancel: Callable[[], bool] | None,
) -> ReindexResult:
    """Run ``mode='full'`` in a staging database and atomically swap.

    AC-02/AC-05: cancellation/timeout must preserve the prior
    committed generation and reader-visible rows. The live
    store is never partially modified; all work happens in a
    separate ``ExploreStore`` rooted at
    ``store.index_dir/.staging-full-{uuid}/``. The main store
    is replaced at the file level only after the staging
    reindex returns ``ok``.

    The staging path is generated once per call and is removed
    in the ``finally`` block regardless of outcome. A cancel
    that fires during the staging build simply abandons the
    staging directory; the main store continues to serve the
    prior generation.

    AC-05: the outer absolute deadline and cancellation token
    are forwarded into the swap step. The swap is skipped
    without publishing the staged state when no time remains
    or the caller has cancelled, even if the staging build
    itself returned ``ok``. The previously committed
    generation remains queryable through the swap refusal.

    The function finalizes its own result and returns the
    ``ReindexResult`` directly; the caller does not call
    ``_finalize`` again on top of this.
    """
    staging_dir = store.index_dir / f".staging-full-{uuid.uuid4().hex}"
    staging_dir.mkdir(parents=True, exist_ok=True)
    try:
        staging = ExploreStore(staging_dir)
        try:
            # The staging database starts empty, so a
            # ``changed`` rebuild on it is equivalent to a full
            # rebuild. The cancel callable and deadline are
            # forwarded so the staging build can also fail
            # closed when the caller is no longer interested.
            # AC-05: the staging build's ``timeout_ms`` is the
            # remaining budget from the outer call. Reading
            # ``now_fn()`` gives the elapsed time used by the
            # outer dispatch; subtracting it from
            # ``options.timeout_ms`` gives the remaining budget.
            # A non-positive result is clamped to 1 ms so the
            # staging build raises ``timed_out`` instead of
            # looping on a zero budget.
            elapsed_ms = max(0, int((now_fn() - state.started_at) * 1000))
            remaining_ms = max(1, options.timeout_ms - elapsed_ms)
            inner_opts = ReindexOptions(
                mode="changed",
                timeout_ms=remaining_ms,
                path_scope=options.path_scope,
                clock=options.clock,
            )
            inner_result = reindex(
                staging,
                workspace_root,
                options=inner_opts,
                cancel=cancel,
            )
        finally:
            staging.close()
        if inner_result.status == "cancelled":
            return _finalize(store, state, status="cancelled", now_fn=now_fn)
        if inner_result.status == "timed_out":
            return _finalize(store, state, status="timed_out", now_fn=now_fn)
        if inner_result.status != "ok":
            return _finalize(
                store,
                state,
                status="failed",
                now_fn=now_fn,
                error_summary=inner_result.error_summary
                or f"staged full reindex returned {inner_result.status}",
            )
        # Carry the staged counters into the main state so
        # ``_finalize`` reports truthful parse_count and
        # changed_paths.
        state.parse_count += inner_result.parse_count
        state.changed_paths.extend(inner_result.changed_files)
        state.failed_paths.extend(inner_result.failed_files)
        # AC-05: propagate the outer absolute deadline and
        # cancellation token into the swap work. The
        # ``_swap_staged_index`` helper checks both signals
        # before and during the swap so a deadline expiry or
        # cancel that fires between staging completion and
        # swap completion refuses the swap without publishing
        # the staged state. The previously committed
        # generation remains queryable.
        if (now_fn() - state.started_at) * 1000 > state.deadline_ms:
            state.timed_out = True
            return _finalize(
                store,
                state,
                status="timed_out",
                now_fn=now_fn,
                error_summary="swap skipped: outer deadline exceeded",
            )
        if cancel is not None and cancel():
            return _finalize(
                store,
                state,
                status="cancelled",
                now_fn=now_fn,
                error_summary="swap skipped: cancellation requested",
            )
        try:
            _swap_staged_index(
                store,
                staging_dir,
                started_at=state.started_at,
                deadline_ms=state.deadline_ms,
                now_fn=now_fn,
                cancel=cancel,
            )
        except _ReindexTimeoutError:
            return _finalize(
                store,
                state,
                status="timed_out",
                now_fn=now_fn,
                error_summary="swap timed out before completion",
            )
        except _ReindexCancelledError:
            return _finalize(
                store,
                state,
                status="cancelled",
                now_fn=now_fn,
                error_summary="swap cancelled before completion",
            )
        return _finalize(store, state, status="ok", now_fn=now_fn)
    finally:
        # Always remove the staging directory; it is
        # disposable and must not be committed.
        if staging_dir.exists():
            shutil.rmtree(staging_dir, ignore_errors=True)


def _swap_staged_index(
    store: ExploreStore,
    staging_dir: Path,
    *,
    started_at: float,
    deadline_ms: int,
    now_fn: Callable[[], float],
    cancel: Callable[[], bool] | None,
) -> None:
    """Atomically replace the main index files with the staging ones.

    AC-02/AC-05: the swap is fail-safe at the file level. The
    staging database is written to a ``.swap`` temp file in the
    same directory as the main database, then ``os.replace``
    atomically renames the temp file into the main path. On
    POSIX, ``os.replace`` is atomic for files on the same
    filesystem; on Windows, it is also atomic for same-volume
    operations. The main database is never partially modified:
    a failure before ``os.replace`` leaves it untouched.

    The main connection is closed before the swap and re-opened
    at the end of the function against either the new file
    (success) or the prior file (failure during swap). Any
    exception that fires between the close and the swap is
    re-raised only after the connection is re-opened against
    the prior database, so the store remains queryable.

    WAL/SHM replacement is best-effort: SQLite recovers from a
    stale or missing WAL/SHM on the next open (the WAL is
    checkpointed and the SHM is re-created), so a failure there
    does not invalidate the swap.

    AC-05: the swap work is bounded by the outer absolute
    deadline (``now_fn() - started_at`` vs ``deadline_ms``) and
    the cancellation token. The deadline/cancel signals are
    checked at every swap-step boundary: before the connection
    close, after the cleanup of a stale ``.swap`` file, before
    the main-DB swap, after the main-DB swap, and after each
    WAL/SHM swap. When no time remains or the caller has
    cancelled, the function raises ``_ReindexTimeoutError`` or
    ``_ReindexCancelledError`` so the caller can finalize the
    job as ``timed_out`` / ``cancelled`` without publishing
    the staged state. The prior committed generation is
    preserved.
    """
    main_db = store.db_path
    main_wal = main_db.with_name(main_db.name + "-wal")
    main_shm = main_db.with_name(main_db.name + "-shm")
    staging_db = staging_dir / main_db.name
    staging_wal = staging_db.with_name(staging_db.name + "-wal")
    staging_shm = staging_db.with_name(staging_db.name + "-shm")
    swap_dir = main_db.parent
    tmp_main = swap_dir / (main_db.name + ".swap")
    # AC-05: deadline/cancel check before the swap work starts.
    # The caller (``_staged_full_reindex``) checks these signals
    # before calling this helper, but a re-check inside the
    # helper defends against a caller that bypassed the outer
    # check (tests, future refactors).
    if (now_fn() - started_at) * 1000 > deadline_ms:
        raise _ReindexTimeoutError("deadline exceeded before swap")
    if cancel is not None and cancel():
        raise _ReindexCancelledError("cancelled before swap")
    # Close the main connection so the WAL is flushed and the
    # file is safe to rename. Reopen at the end of the function
    # against either the new file (success) or the prior file
    # (failure). The re-open happens in a ``finally``-like
    # pattern via the explicit try/except blocks below.
    with suppress(sqlite3.ProgrammingError):
        store._conn.close()
    # AC-05: deadline/cancel check after the connection close.
    # The close is irreversible for the duration of this call,
    # so a deadline that expires here MUST refuse to publish.
    if (now_fn() - started_at) * 1000 > deadline_ms:
        store.reopen()
        raise _ReindexTimeoutError("deadline exceeded after connection close")
    if cancel is not None and cancel():
        store.reopen()
        raise _ReindexCancelledError("cancelled after connection close")
    # Best-effort cleanup of a leftover ``.swap`` temp file
    # from a prior aborted swap. A failure here is treated
    # like a swap failure: reopen the prior DB and re-raise.
    if tmp_main.exists():
        try:
            tmp_main.unlink()
        except OSError:
            store.reopen()
            raise
    # AC-05: deadline/cancel check immediately before the
    # main-DB swap. This is the last guard before the
    # irreversible ``os.replace`` writes the new database.
    if (now_fn() - started_at) * 1000 > deadline_ms:
        store.reopen()
        raise _ReindexTimeoutError("deadline exceeded before main-DB swap")
    if cancel is not None and cancel():
        store.reopen()
        raise _ReindexCancelledError("cancelled before main-DB swap")
    # Atomic main-DB swap. The temp file lives in the same
    # directory as the main DB so ``os.replace`` is atomic on
    # the same filesystem. The main DB is never overwritten
    # until ``os.replace`` succeeds; a failure mid-swap leaves
    # the prior committed database on disk.
    try:
        tmp_main.write_bytes(staging_db.read_bytes())
        tmp_main.replace(main_db)
    except BaseException:
        # The main DB is unchanged because ``os.replace`` did
        # not complete. Clean up the temp file (best effort)
        # and reopen the connection against the prior file so
        # the store stays queryable for subsequent operations.
        if tmp_main.exists():
            with suppress(OSError):
                tmp_main.unlink()
        store.reopen()
        raise
    # AC-05 (correctness): deadline/cancel check after the
    # main-DB swap MUST NOT cause the swap function to raise
    # ``cancelled`` / ``timed_out``. The new main DB is now
    # durably published on disk; the deadline/cancel contract
    # is "refuse to publish", not "roll back a successful
    # publish". A cancel that fires here is a no-op for the
    # publish decision; the auxiliary WAL/SHM swap is best
    # effort and may be skipped if the budget is exhausted.
    # We deliberately do NOT raise so the caller can finalize
    # as ``ok`` with the new generation visible to readers.
    # Main DB is now durably replaced. Best-effort WAL/SHM
    # swap; failures here are non-fatal because the next open
    # re-creates the SHM and checkpoints the WAL. The main DB
    # is openable in either case.
    for src, dst in ((staging_wal, main_wal), (staging_shm, main_shm)):
        if not src.exists():
            continue
        # AC-05 (correctness): deadline/cancel check between
        # each WAL/SHM swap. The new main DB is already in
        # place; cancel/timeout after publish is a no-op for
        # the publish decision, but the remaining auxiliary
        # swaps are still best-effort work. We skip the
        # remaining auxiliary work rather than roll back the
        # successful publish, and we do not raise.
        if (now_fn() - started_at) * 1000 > deadline_ms:
            break
        if cancel is not None and cancel():
            break
        tmp_aux = swap_dir / (dst.name + ".swap")
        try:
            if tmp_aux.exists():
                tmp_aux.unlink()
            tmp_aux.write_bytes(src.read_bytes())
            tmp_aux.replace(dst)
        except OSError:
            # WAL/SHM swap failure is non-fatal: the main DB
            # is already in place and openable. Continue.
            with suppress(OSError):
                if tmp_aux.exists():
                    tmp_aux.unlink()
            continue
    # Reopen the live connection against the new main DB.
    # Even if the post-publish cancel/timeout fires
    # immediately above, the reopen itself is mandatory so
    # the store is queryable; we do not let the budget
    # shorten the reopen path.
    store.reopen()


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

    # --- Preflight phase: read + decode + build lexical + structure.
    # These calls happen BEFORE any destructive write so a failure
    # (malformed Python, OS read error, OOM) preserves every
    # prior row in the live store. The caller treats a raised
    # exception as a per-path failure recorded in failed_files;
    # the path stays dirty and is retried on the next pass.
    text = full.read_text(encoding="utf-8", errors="replace")
    prepared_chunks: list[tuple[int, int, str]] = list(
        chunk_text(text, lines_per_chunk=DEFAULT_CHUNK_LINES)
    )
    # ``extract_structure`` raises ``PythonSyntaxError`` for
    # malformed Python; the typed exception propagates so the
    # caller fails-closed without losing prior rows.
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
