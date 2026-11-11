
from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from pathlib import Path

from ralph.mcp.explore import _pipeline_run
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
from ralph.mcp.explore._store_types import hash_workspace_file
from ralph.mcp.explore.store import (
    Clock,
    ExploreStore,
    SystemClock,
)

logger = logging.getLogger(__name__)




def reindex(
    store: ExploreStore,
    workspace_root: Path,
    *,
    options: ReindexOptions | None = None,
    cancel: Callable[[], bool] | None = None,
) -> ReindexResult:
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
        if opts.mode == "full":
            staged_result = _staged_full_reindex(
                store,
                workspace_root,
                options=opts,
                now_fn=now_fn,
                state=state,
                cancel=cancel,
            )
            return _finalize_with_recovery(
                store, state, workspace_root, now_fn=now_fn, result=staged_result
            )
        status = _pipeline_run._run_reindex(
            store,
            workspace_root,
            options=opts,
            now_fn=now_fn,
            state=state,
            cancel=cancel,
        )
    except _ReindexTimeoutError:
        return _finalize_with_recovery(
            store, state, workspace_root, now_fn=now_fn, status="timed_out"
        )
    except _ReindexCancelledError:
        return _finalize_with_recovery(
            store, state, workspace_root, now_fn=now_fn, status="cancelled"
        )
    except Exception as exc:
        return _finalize_with_recovery(
            store,
            state,
            workspace_root,
            now_fn=now_fn,
            status="failed",
            error_summary=f"{type(exc).__name__}: {exc}",
        )
    return _finalize_with_recovery(store, state, workspace_root, now_fn=now_fn, status=status)


def _finalize_with_recovery(
    store: ExploreStore,
    state: _ReindexState,
    workspace_root: Path,
    *,
    now_fn: Callable[[], float],
    status: str | None = None,
    result: ReindexResult | None = None,
    error_summary: str | None = None,
) -> ReindexResult:
    if isinstance(result, ReindexResult):
        finalized = result
    else:
        finalized = _finalize(
            store,
            state,
            status=status if status is not None else "failed",
            now_fn=now_fn,
            error_summary=error_summary,
        )
    if finalized.status == "timed_out" and finalized.generation == 0:
        try:
            from ralph.mcp.explore.recovery import enqueue_recovery

            enqueue_recovery(
                workspace_root,
                "timeout_exceeded",
                message=f"timed-out cold reindex job={state.job_id}",
            )
        except Exception:
            pass
    return finalized


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
