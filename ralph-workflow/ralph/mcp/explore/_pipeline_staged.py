"""Staged full-reindex path for the indexed exploration substrate.

Extracted from :mod:`ralph.mcp.explore.pipeline` so the hub module
stays under the per-file line ceiling. The staged path keeps a
staged SQLite index on disk and atomically swaps metadata only
after the staged rebuild succeeds; readers see the last committed
generation throughout.
"""

from __future__ import annotations

import logging
import shutil
import sqlite3
import uuid
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path

from ralph.mcp.explore._pipeline_state import (
    ReindexOptions,
    ReindexResult,
    _ReindexCancelledError,
    _ReindexState,
    _ReindexTimeoutError,
)
from ralph.mcp.explore.store import (
    ExploreStore,
)

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
            # Lazy import to avoid the circular import between
            # ``_pipeline_staged`` and ``pipeline``: ``pipeline``
            # imports ``_staged_full_reindex`` to dispatch the
            # full mode; importing ``reindex`` back at module
            # scope would form a cycle that fails to load.
            from ralph.mcp.explore.pipeline import reindex
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
