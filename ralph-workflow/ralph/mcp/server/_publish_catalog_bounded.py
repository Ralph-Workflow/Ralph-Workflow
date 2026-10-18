"""Bounded catalog publish helper for the standalone server pre-serve critical path.

R2 WU-1: ``publish_tool_catalog_to_parent`` (in
``ralph/mcp/upstream/tool_catalog_cache.py``) opens the predictable
``.agent/mcp/parent_catalog.json.tmp`` path with a synchronous
``os.open``. A FIFO replacement (hostile filesystem, stale writer)
blocks the open indefinitely and holds the GIL; the interpreter
cannot interrupt the worker mid-call, so the only correct
fail-closed is to bypass interpreter cleanup: log the typed
error, record the last stage, and ``os._exit`` so the OS reaps
the worker via process termination. The parent owns the
listening socket for the bridge's lifetime, so a force-exited
child releases it without an ``ECONNREFUSED`` window.

The helper runs the publish in a thread pool with the remaining
whole-startup deadline; on timeout, ``os._exit`` with the
distinct ``CATALOG_PUBLISH_OVERRUN_EXIT_CODE`` so the parent
restart budget attributes the exit to this stage without
re-reading the on-disk stage file.
"""

from __future__ import annotations

import concurrent.futures
import time
from typing import TYPE_CHECKING

from ralph.mcp.protocol._startup_timeouts import (
    _READY_TO_SERVE_BUDGET_S,
    mcp_whole_startup_budget_from_env,
)
from ralph.mcp.server._pre_serve_overrun import (
    CATALOG_PUBLISH_OVERRUN_EXIT_CODE,
    fail_closed_pre_serve_overrun,
)
from ralph.mcp.upstream.tool_catalog_cache import publish_tool_catalog_to_parent

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from ralph.mcp.upstream.upstream_tool import UpstreamTool


def publish_tool_catalog_bounded(
    workspace_root: Path,
    catalog: dict[str, list[UpstreamTool]],
    env_map: Mapping[str, str],
    t0: float | None = None,
) -> None:
    """Run ``publish_tool_catalog_to_parent`` with the remaining whole-startup deadline.

    Returns normally on success. On timeout (or zero remaining
    budget), the process is force-exited via ``os._exit`` with
    the distinct ``CATALOG_PUBLISH_OVERRUN_EXIT_CODE`` so the
    parent restart budget attributes the exit to the
    ``upstream_catalog_or_discovery_done`` stage without
    re-reading the on-disk stage file.
    """
    whole_budget_s = mcp_whole_startup_budget_from_env(env_map)
    if t0 is None:
        t0 = time.monotonic()
    remaining_s = max(0.0, whole_budget_s - (time.monotonic() - t0))
    effective_timeout_s = min(_READY_TO_SERVE_BUDGET_S, remaining_s)
    if effective_timeout_s <= 0.0:
        fail_closed_pre_serve_overrun(
            workspace_root,
            stage="upstream_catalog_or_discovery_done",
            elapsed_s=time.monotonic() - t0,
            budget_s=0.0,
            exit_code=CATALOG_PUBLISH_OVERRUN_EXIT_CODE,
        )
    ex: concurrent.futures.ThreadPoolExecutor | None = None
    try:
        ex = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        future = ex.submit(
            publish_tool_catalog_to_parent,
            workspace_root,
            catalog,
        )
        future.result(timeout=effective_timeout_s)
    except concurrent.futures.TimeoutError:
        # GIL-bound worker on FIFO blocks shutdown(wait=False); the
        # GIL blocks all cleanup, so the parent's snapshot reader
        # treats the partial as malformed and the workspace as
        # having no catalog. Inherited parent listener keeps
        # accepting connections in the kernel backlog until the
        # kernel reaps us.
        fail_closed_pre_serve_overrun(
            workspace_root,
            stage="upstream_catalog_or_discovery_done",
            elapsed_s=time.monotonic() - t0,
            budget_s=effective_timeout_s,
            exit_code=CATALOG_PUBLISH_OVERRUN_EXIT_CODE,
        )
    finally:
        if ex is not None:
            ex.shutdown(wait=False)  # do not accumulate executor threads across calls


__all__ = ["publish_tool_catalog_bounded"]
