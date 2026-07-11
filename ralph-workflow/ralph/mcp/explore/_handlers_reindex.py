"""MCP handler for ``ralph_reindex``.

Extracted from :mod:`ralph.mcp.explore.handlers` so the hub module
stays under the repository\'s per-file line ceiling. The handler is
the only public surface; the helper ``_build_reindex_payload`` is
implementation detail and remains importable from this module for
test reach.

Cancel-token wiring uses the shared ``_new_cancel_token`` /
``_arm_cancel_flag`` / ``_disarm_cancel_flag`` helpers in
:mod:`ralph.mcp.explore.handlers` so both the reindex and graph
handlers share one source of truth.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, cast

from ralph.mcp.explore import handlers as handlers_module
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
from ralph.mcp.explore._pipeline_writer import ReindexWriter
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


#: Per-request cancel flag for ralph_reindex. Mirrors the
#: shared ``_new_cancel_token`` / ``_arm_cancel_flag`` helpers;
#: one entry per active call, keyed by a unique request token,
#: cleared on every exit path. The token is generated at the
#: start of every call and removed in the ``finally`` block, so
#: a previous caller's cancel cannot poison a concurrent reindex
#: against the same session and the map cannot leak across
#: long-lived sessions.
_REINDEX_CANCEL_FLAGS: dict[str, bool] = {}  # bounded-accumulator-ok: keyed by request token; one entry per active call
_REINDEX_CANCEL_LOCK: threading.Lock = threading.Lock()


def handle_ralph_reindex(
    session: CoordinationSessionLike,
    workspace: object,
    params: dict[str, object],
) -> ToolResult:
    """Run a bounded changed/full reindex.

    Capability: ``WorkspaceRead`` (the reindex touches workspace
    files). Production callers are expected to gate reindex behind a
    higher privilege in the future; the Phase 1 contract keeps the
    current capability.

    AC-05: ``timeout_ms`` is bounded. The handler rejects values
    outside ``[1, _REINDEX_TIMEOUT_MAX_MS]`` rather than forwarding
    arbitrarily large values into ``ReindexOptions``. Malformed
    (non-integer, non-string-int) values are also rejected; callers
    must send a positive integer in the documented range.
    """
    require_capability(session, WORKSPACE_READ_CAPABILITY, "Explore reindex")
    mode_raw: object = params.get("mode", "changed")
    mode: str = str(mode_raw) if not isinstance(mode_raw, str) else mode_raw
    if mode not in {"changed", "full"}:
        raise InvalidParamsError(
            f"Invalid reindex mode: {mode!r}; expected 'changed' or 'full'"
        )
    # AC-05: bounded per-call budget. Malformed or out-of-range
    # values fail closed with a structured tool error; the
    # dispatcher must NOT silently fall back to the default when
    # the caller sent garbage.
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
                # AC-05: surface invalid path_scope as a structured
                # tool error before reindexing rather than letting
                # the rejection propagate as a generic exception.
                raise InvalidParamsError(
                    f"Invalid path_scope entry {p!r}: {exc}"
                ) from exc
        path_scope = tuple(normalized_scope)

    workspace_root_obj2: object = getattr(workspace, "root", None)
    workspace_root_raw2: object = workspace_root_obj2 or params.get(
        "workspace_root", ""
    )
    workspace_root_str2: str = (
        str(workspace_root_raw2) if workspace_root_raw2 else ""
    )
    workspace_root = Path(workspace_root_str2) if workspace_root_str2 else Path.cwd()

    handle: ExploreIndex | None = handlers_module._resolve_explore_index(session)
    cold_built = False
    if handle is None:
        handle = handlers_module.build_explore_index(workspace_root)
        # The first call from a session without an explore index is
        # typically the cold build; tag it so downstream consumers
        # can decide whether to block on it.
        handle.cold_index_required = handle.generation == 0
        cold_built = True
        # AC-03: persist the cold-built handle on the session so
        # subsequent indexed read/search/grep/list/edit operations
        # observe the same handle (and therefore the same generation
        # + dirty-path state). Also surface it on the workspace so
        # helpers that take the workspace object (e.g. the file
        # mutation handlers) can find it without re-walking the
        # session.
        import contextlib

        with contextlib.suppress(Exception):
            session.explore_index = handle
        with contextlib.suppress(Exception):
            # The ``workspace`` parameter is typed as ``object`` to
            # avoid pulling the full Workspace protocol into this
            # handler. Production workspaces expose ``explore_index``
            # via the Workspace protocol; the attribute assignment is
            # wrapped in suppress so legacy workspaces stay valid.
            # Cast to the Workspace protocol so direct attribute
            # assignment is the canonical, non-setattr path; the
            # assignment still goes through the protocol's optional
            # surface so legacy workspaces can ignore the attribute
            # without errors.
            cast("Workspace", workspace).explore_index = handle
    _ = cold_built  # reserved for future payload/audit fields


    started_at = time.time()
    options = ReindexOptions(
        mode=mode,
        timeout_ms=timeout_ms,
        path_scope=path_scope,
    )
    # AC-05: bounded cancel contract for ralph_reindex. The schema
    # exposes ``cancel: bool``; when set, the handler installs a
    # per-request cancel flag (keyed by a fresh token) that the
    # reindex writer polls at phase boundaries. Concurrent
    # reindex calls against the same session get distinct tokens,
    # so one caller's cancel never cancels or clears another
    # caller's flag. On cancel the prior committed generation is
    # preserved (no mutable work is exposed) and the response
    # carries ``cancelled=true`` with a bounded incomplete
    # summary.
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
        # AC-02 / AC-05: route the public ``ralph_reindex`` tool
        # through :meth:`ReindexWriter.claim` so concurrent MCP
        # requests, parallel lifecycle hooks, and a second
        # ``ralph_reindex`` call against the same store coalesce
        # into a single writer. The writer's ``claim`` preserves
        # cancellation/deadline semantics and dirty-path
        # coalescing; the per-request cancel flag in
        # ``_REINDEX_CANCEL_FLAGS`` still propagates through to
        # ``reindex`` because the cancel callable is forwarded
        # via ``ReindexWriter.claim``.
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
        # AC-02/AC-05: clear the per-request cancel flag at every
        # exit path so a previous caller's cancel cannot poison a
        # subsequent reindex against the same session. The token
        # is unique to this call, so the pop never deletes a
        # concurrent caller's flag.
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

