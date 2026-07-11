"""Idempotent, work-proportional reindex pipeline.

The shipped pipeline indexes lexical chunks plus deterministic Python and
Markdown structure, symbols, and provenance-labelled graph edges. Optional
Phase 5 adapters remain deferred; the core extraction path has no LLM, vector,
or third-party parser dependency.

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
import uuid
from collections.abc import Callable
from pathlib import Path

# Ponytail: keep imports narrow; nothing from the mcp package is
# imported here so audit_mcp_timeout stays happy without markers.
# Use the shared state types from _pipeline_state so the dataclasses,
# exception classes, and constants are shared across modules
# (avoids duplicate-class identity bugs and mypy
# incompatible-return-type errors).
from ralph.mcp.explore._pipeline_run import _run_reindex
from ralph.mcp.explore._pipeline_staged import (
    _finalize,
    _staged_full_reindex,
)
from ralph.mcp.explore._pipeline_state import (
    DEFAULT_FULL_TIMEOUT_MS,
    DEFAULT_TIMEOUT_MS,
    EXTRACTOR_VERSION,
    ReindexOptions,
    ReindexResult,
    _ReindexCancelledError,
    _ReindexState,
    _ReindexTimeoutError,
)
from ralph.mcp.explore._pipeline_writer import ReindexWriter

# Re-export hash_workspace_file so test patches
# ``ralph.mcp.explore.pipeline.hash_workspace_file = ...`` keep working.
from ralph.mcp.explore._store_types import hash_workspace_file
from ralph.mcp.explore.store import (
    Clock,
    ExploreStore,
    SystemClock,
)

logger = logging.getLogger(__name__)


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


__all__ = [
    "DEFAULT_FULL_TIMEOUT_MS",
    "DEFAULT_TIMEOUT_MS",
    "EXTRACTOR_VERSION",
    "ReindexOptions",
    "ReindexResult",
    "ReindexWriter",
    "hash_workspace_file",
    "reindex",
]
