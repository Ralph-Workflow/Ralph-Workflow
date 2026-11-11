
from __future__ import annotations

import logging
import shutil
import sqlite3
import uuid
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path

from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND, FileBackend
from ralph.mcp.artifacts.idempotent_write import atomic_write_bytes_if_changed
from ralph.mcp.explore._pipeline_state import (
    ReindexOptions,
    ReindexResult,
    _ReindexCancelledError,
    _ReindexState,
    _ReindexTimeoutError,
)
from ralph.mcp.explore._store_types import real_clock_seconds
from ralph.mcp.explore.store import ExploreStore

logger = logging.getLogger(__name__)


def _staged_full_reindex(
    store: ExploreStore,
    workspace_root: Path,
    *,
    options: ReindexOptions,
    now_fn: Callable[[], float],
    state: _ReindexState,
    cancel: Callable[[], bool] | None,
) -> ReindexResult:
    staging_dir = store.index_dir / f".staging-full-{uuid.uuid4().hex}"
    staging_dir.mkdir(parents=True, exist_ok=True)
    try:
        staging = ExploreStore(staging_dir)
        try:
            elapsed_ms = max(0, int((now_fn() - state.started_at) * 1000))
            remaining_ms = max(1, options.timeout_ms - elapsed_ms)
            inner_opts = ReindexOptions(
                mode="changed",
                timeout_ms=remaining_ms,
                path_scope=options.path_scope,
                clock=options.clock,
                structure_extractor=options.structure_extractor,
            )
            from ralph.mcp.explore.pipeline import reindex

            inner_result = reindex(
                staging,
                workspace_root,
                options=inner_opts,
                cancel=cancel,
            )
            with suppress(Exception):
                staging._conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
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
        state.parse_count += inner_result.parse_count
        state.changed_paths.extend(inner_result.changed_files)
        state.failed_paths.extend(inner_result.failed_files)
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
    backend: FileBackend = DEFAULT_FILE_BACKEND,
) -> None:
    main_db = store.db_path
    main_wal = main_db.with_name(main_db.name + "-wal")
    main_shm = main_db.with_name(main_db.name + "-shm")
    staging_db = staging_dir / main_db.name
    staging_wal = staging_db.with_name(staging_db.name + "-wal")
    staging_shm = staging_db.with_name(staging_db.name + "-shm")
    swap_dir = main_db.parent
    tmp_main = swap_dir / (main_db.name + ".swap")
    if (now_fn() - started_at) * 1000 > deadline_ms:
        raise _ReindexTimeoutError("deadline exceeded before swap")
    if cancel is not None and cancel():
        raise _ReindexCancelledError("cancelled before swap")
    try:
        with suppress(sqlite3.ProgrammingError):
            store._conn.close()
    except BaseException:
        store.reopen()
        raise
    if (now_fn() - started_at) * 1000 > deadline_ms:
        store.reopen()
        raise _ReindexTimeoutError("deadline exceeded after connection close")
    if cancel is not None and cancel():
        store.reopen()
        raise _ReindexCancelledError("cancelled after connection close")
    if tmp_main.exists():
        try:
            tmp_main.unlink()
        except OSError:
            store.reopen()
            raise
    if (now_fn() - started_at) * 1000 > deadline_ms:
        store.reopen()
        raise _ReindexTimeoutError("deadline exceeded before main-DB swap")
    if cancel is not None and cancel():
        store.reopen()
        raise _ReindexCancelledError("cancelled before main-DB swap")
    try:
        atomic_write_bytes_if_changed(
            backend,
            main_db,
            staging_db.read_bytes(),
            tmp_path=tmp_main,
        )
    except BaseException:
        if tmp_main.exists():
            with suppress(OSError):
                tmp_main.unlink()
        store.reopen()
        raise
    for src, dst in ((staging_wal, main_wal), (staging_shm, main_shm)):
        if not src.exists():
            continue
        if (now_fn() - started_at) * 1000 > deadline_ms:
            break
        if cancel is not None and cancel():
            break
        tmp_aux = swap_dir / (dst.name + ".swap")
        try:
            if tmp_aux.exists():
                tmp_aux.unlink()
            atomic_write_bytes_if_changed(
                backend,
                dst,
                src.read_bytes(),
                tmp_path=tmp_aux,
            )
        except OSError:
            with suppress(OSError):
                if tmp_aux.exists():
                    tmp_aux.unlink()
            continue
    for aux in (main_wal, main_shm):
        if not aux.exists():
            continue
        try:
            if not aux.resolve().is_relative_to(swap_dir.resolve()):
                continue
        except (OSError, AttributeError):
            continue
        with suppress(FileNotFoundError):
            aux.unlink()
    store.reopen()


def _finalize(
    store: ExploreStore,
    state: _ReindexState,
    *,
    status: str,
    now_fn: Callable[[], float],
    error_summary: str | None = None,
) -> ReindexResult:
    finished_monotonic = now_fn()
    elapsed_seconds = finished_monotonic - state.started_at
    finished_at = real_clock_seconds()
    error = error_summary if error_summary is not None else state.error_summary
    store.record_job(
        job_id=state.job_id,
        generation=int(store.get_setting("current_generation") or 0),
        status=status,
        started_at=finished_at - elapsed_seconds,
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
        elapsed_seconds=elapsed_seconds,
        error_summary=error,
    )
