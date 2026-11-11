
from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, cast

from ralph.mcp.explore import handlers as handlers_module
from ralph.mcp.explore._pipeline_writer import ReindexWriter
from ralph.mcp.explore.handlers import (
    ExploreIndex,
    _new_cancel_token,
    _strict_int_param,
)
from ralph.mcp.explore.pipeline import (
    DEFAULT_FULL_TIMEOUT_MS as _REINDEX_TIMEOUT_MAX_MS,
)
from ralph.mcp.explore.pipeline import (
    DEFAULT_TIMEOUT_MS,
    ReindexOptions,
    ReindexResult,
)
from ralph.mcp.explore.store import normalize_index_path
from ralph.mcp.tools.coordination import (
    CoordinationSessionLike,
    InvalidParamsError,
    ToolContent,
    ToolResult,
    require_capability,
)
from ralph.mcp.tools.workspace._utils import (
    WORKSPACE_READ_CAPABILITY,
    _tool_json,
)

if TYPE_CHECKING:
    from ralph.workspace.protocol import Workspace


logger = logging.getLogger(__name__)


_REINDEX_CANCEL_FLAGS: dict[
    str, bool
] = {}
_REINDEX_CANCEL_LOCK: threading.Lock = threading.Lock()


def handle_ralph_reindex(
    session: CoordinationSessionLike,
    workspace: object,
    params: dict[str, object],
) -> ToolResult:
    require_capability(session, WORKSPACE_READ_CAPABILITY, "Explore reindex")
    mode_raw: object = params.get("mode", "changed")
    mode: str = str(mode_raw) if not isinstance(mode_raw, str) else mode_raw
    if mode not in {"changed", "full"}:
        raise InvalidParamsError(f"Invalid reindex mode: {mode!r}; expected 'changed' or 'full'")
    timeout_ms = _strict_int_param(
        params,
        "timeout_ms",
        default=DEFAULT_TIMEOUT_MS,
        min_value=1,
        max_value=_REINDEX_TIMEOUT_MAX_MS,
    )
    path_scope_raw: object = params.get("path_scope")
    path_scope: tuple[str, ...] = ()
    if isinstance(path_scope_raw, list):
        normalized_scope: list[str] = []
        for p in path_scope_raw:
            if not isinstance(p, (str, int, float)):
                continue
            try:
                normalized_scope.append(normalize_index_path(str(p)))
            except ValueError as exc:
                raise InvalidParamsError(f"Invalid path_scope entry {p!r}: {exc}") from exc
        path_scope = tuple(normalized_scope)

    workspace_root_obj2: object = getattr(workspace, "root", None)
    workspace_root_raw2: object = workspace_root_obj2 or params.get("workspace_root", "")
    workspace_root_str2: str = str(workspace_root_raw2) if workspace_root_raw2 else ""
    workspace_root = Path(workspace_root_str2) if workspace_root_str2 else Path.cwd()

    handle: ExploreIndex | None = handlers_module._resolve_explore_index(session)
    cold_built = False
    if handle is None:
        handle = handlers_module.build_explore_index(workspace_root)
        handle.cold_index_required = handle.generation == 0
        cold_built = True
        import contextlib

        with contextlib.suppress(Exception):
            session.explore_index = handle
        with contextlib.suppress(Exception):
            cast(
                "Workspace", workspace
            ).explore_index = handle
    _ = cold_built

    started_at = time.time()
    options = ReindexOptions(
        mode=mode,
        timeout_ms=timeout_ms,
        path_scope=path_scope,
    )
    cancel_raw: object = params.get("cancel", False)
    cancel_flag = bool(cancel_raw) if isinstance(cancel_raw, bool) else False
    reindex_cancel_token = _new_cancel_token()
    cancel_callable: Callable[[], bool] = handlers_module._arm_cancel_flag(
        _REINDEX_CANCEL_FLAGS,
        _REINDEX_CANCEL_LOCK,
        reindex_cancel_token,
        cancel_flag,
    )
    try:
        result = ReindexWriter.claim(
            handle.store,
            workspace_root=handle.workspace_root,
            options=options,
            cancel=cancel_callable,
        )
    except Exception as exc:
        logger.exception("ralph_reindex crashed: %s", exc)
        return ToolResult(
            content=[
                ToolContent.text_content(
                    _tool_json(
                        {
                            "status": "failed",
                            "error": f"{type(exc).__name__}: {exc}",
                            "elapsed_seconds": time.time() - started_at,
                        }
                    )
                )
            ],
            is_error=True,
        )
    finally:
        handlers_module._disarm_cancel_flag(
            _REINDEX_CANCEL_FLAGS,
            _REINDEX_CANCEL_LOCK,
            reindex_cancel_token,
        )
    handle.generation = result.generation
    handle.last_job_status = result.status
    handle.last_refresh_kind = "full" if mode == "full" else "changed"
    payload = _build_reindex_payload(result)
    payload["cancelled"] = result.status == "cancelled"
    from ralph.mcp.explore.serving import serving_metadata

    payload.update(
        serving_metadata(
            session,
            index_used=result.status not in {"failed", "cancelled"},
            fallback_reason=(
                None
                if result.status not in {"failed", "cancelled"}
                else "interrupted_build"
                if result.status == "cancelled"
                else "indexer_error"
            ),
        )
    )
    return ToolResult(
        content=[ToolContent.text_content(_tool_json(payload))],
        is_error=False,
    )


def _build_reindex_payload(result: ReindexResult) -> dict[str, object]:
    return {
        "job_id": result.job_id,
        "job_status": result.status,
        "generation": result.generation,
        "changed_files": list(result.changed_files),
        "failed_files": list(result.failed_files),
        "parse_count": result.parse_count,
        "dirty_paths_count": result.dirty_paths_count,
        "elapsed_seconds": result.elapsed_seconds,
        "error_summary": result.error_summary,
    }
