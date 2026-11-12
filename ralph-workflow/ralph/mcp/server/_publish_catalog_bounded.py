from __future__ import annotations

import concurrent.futures
import time
from typing import TYPE_CHECKING

from ralph.mcp.protocol._startup_timeouts import (
    mcp_real_startup_deadline_from_env,
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
    real_deadline_s = mcp_real_startup_deadline_from_env(env_map)
    if t0 is None:
        t0 = time.monotonic()
    remaining_s = max(0.0, real_deadline_s - (time.monotonic() - t0))
    effective_timeout_s = remaining_s
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
        fail_closed_pre_serve_overrun(
            workspace_root,
            stage="upstream_catalog_or_discovery_done",
            elapsed_s=time.monotonic() - t0,
            budget_s=effective_timeout_s,
            exit_code=CATALOG_PUBLISH_OVERRUN_EXIT_CODE,
        )
    finally:
        if ex is not None:
            ex.shutdown(wait=False)


__all__ = ["publish_tool_catalog_bounded"]
