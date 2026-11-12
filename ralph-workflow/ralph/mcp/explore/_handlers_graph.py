from __future__ import annotations

import contextlib
import threading
import time
from collections.abc import Callable
from pathlib import Path

from ralph.mcp.explore import graph as graph_module
from ralph.mcp.explore import handlers as handlers_module
from ralph.mcp.explore._bounded_flags import BoundedFlagRegistry
from ralph.mcp.explore.graph import run_query
from ralph.mcp.explore.handlers import (
    ExploreIndex,
    _int_param,
    _new_cancel_token,
    _strict_int_param,
)
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

_VALID_GRAPH_QUERY_TYPES: tuple[str, ...] = (
    "neighbors",
    "path",
    "impact",
    "hubs",
    "tests",
)
_VALID_FRESHNESS: tuple[str, ...] = ("required", "prefer_fresh", "allow_stale")
_VALID_CHANGE_KINDS: tuple[str, ...] = (
    "rename",
    "signature",
    "behavior",
    "delete",
    "unknown",
)
_GRAPH_LIMIT_MAX: int = 100
_GRAPH_DEFAULT_TIMEOUT_MS: int = 5_000
_GRAPH_TIMEOUT_MAX_MS: int = 30_000
_GRAPH_CANCEL_FLAGS: BoundedFlagRegistry = BoundedFlagRegistry()
_GRAPH_CANCEL_LOCK: threading.Lock = threading.Lock()


def _graph_node_to_dict(node: graph_module.GraphNode) -> dict[str, object]:
    return {
        "id": node.id,
        "kind": node.kind,
        "label": node.label,
        "path": node.path,
        "confidence": node.confidence,
        "provenance": node.provenance,
        "evidence_ids": list(node.evidence_ids),
    }


def _graph_edge_to_dict(edge: graph_module.GraphEdge) -> dict[str, object]:
    return {
        "source": edge.source,
        "target": edge.target,
        "relation": edge.relation,
        "path": edge.path,
        "confidence": edge.confidence,
        "provenance": edge.provenance,
        "reason": edge.reason,
        "evidence_id": edge.evidence_id,
    }


def _graph_result_to_dict(result: graph_module.GraphResult) -> dict[str, object]:
    return {
        "query_type": result.query_type,
        "nodes": [_graph_node_to_dict(n) for n in result.nodes],
        "edges": [_graph_edge_to_dict(e) for e in result.edges],
        "paths": [list(p) for p in result.paths],
        "impacted_files": list(result.impacted_files),
        "suggested_tests": [_graph_node_to_dict(n) for n in result.suggested_tests],
        "confidence": result.confidence,
        "provenance": result.provenance,
        "evidence_ids": list(result.evidence_ids),
        "missing_data": list(result.missing_data),
        "index_generation": result.index_generation,
        "is_stale": result.is_stale,
        "truncated": result.truncated,
        "cancelled": result.cancelled,
        "deadline_exceeded": result.deadline_exceeded,
        "metadata": dict(result.metadata),
    }


def handle_ralph_graph(
    session: CoordinationSessionLike,
    workspace: object,
    params: dict[str, object],
) -> ToolResult:
    require_capability(session, WORKSPACE_READ_CAPABILITY, "Graph query")
    query_type_raw: object = params.get("query_type", "")
    if not isinstance(query_type_raw, str) or query_type_raw not in _VALID_GRAPH_QUERY_TYPES:
        raise InvalidParamsError(
            f"Invalid query_type: {query_type_raw!r}; expected one of "
            f"{', '.join(_VALID_GRAPH_QUERY_TYPES)}"
        )
    target = str(params.get("target", "")) if params.get("target") is not None else ""
    if not target and query_type_raw in {"neighbors", "path", "impact", "tests"}:
        raise InvalidParamsError(
            f"target is required for query_type={query_type_raw!r}; "
            "only 'hubs' accepts a targetless query."
        )
    target_b_raw = params.get("target_b")
    target_b: str | None = (
        str(target_b_raw) if isinstance(target_b_raw, str) and target_b_raw else None
    )
    relations_raw = params.get("relations")
    relations: tuple[str, ...] | None = None
    if isinstance(relations_raw, list):
        relations = tuple(str(rel) for rel in relations_raw if isinstance(rel, str))
    freshness_raw: object = params.get("freshness", "prefer_fresh")
    freshness: str = str(freshness_raw) if isinstance(freshness_raw, str) else "prefer_fresh"
    if freshness not in _VALID_FRESHNESS:
        raise InvalidParamsError(
            f"Invalid freshness: {freshness!r}; expected one of {', '.join(_VALID_FRESHNESS)}"
        )
    limit = _int_param(params, "limit", 25)
    if limit < 1 or limit > _GRAPH_LIMIT_MAX:
        raise InvalidParamsError("limit must be between 1 and 100")
    direction = str(params.get("direction", "both"))
    if direction not in {"out", "in", "both"}:
        raise InvalidParamsError(
            f"Invalid direction: {direction!r}; expected 'out', 'in', or 'both'"
        )
    depth = _int_param(params, "depth", 1)
    max_paths = _int_param(params, "max_paths", 3)
    change_kind_raw: object = params.get("change_kind", "unknown")
    change_kind: str = str(change_kind_raw) if isinstance(change_kind_raw, str) else "unknown"
    if change_kind not in _VALID_CHANGE_KINDS:
        raise InvalidParamsError(
            f"Invalid change_kind: {change_kind!r}; expected one of "
            f"{', '.join(_VALID_CHANGE_KINDS)}"
        )
    scope_path_raw = params.get("scope_path")
    scope_path: str | None = (
        str(scope_path_raw) if isinstance(scope_path_raw, str) and scope_path_raw else None
    )
    role_raw = params.get("role")
    role: str | None = str(role_raw) if isinstance(role_raw, str) and role_raw else None
    timeout_ms = _strict_int_param(
        params,
        "timeout_ms",
        default=_GRAPH_DEFAULT_TIMEOUT_MS,
        min_value=1,
        max_value=_GRAPH_TIMEOUT_MAX_MS,
    )
    deadline = time.monotonic() + timeout_ms / 1000.0
    cancel_raw: object = params.get("cancel", False)
    cancel_flag = bool(cancel_raw) if isinstance(cancel_raw, bool) else False
    graph_cancel_token = _new_cancel_token()
    cancel_callable: Callable[[], bool] = handlers_module._arm_cancel_flag(
        _GRAPH_CANCEL_FLAGS,
        _GRAPH_CANCEL_LOCK,
        graph_cancel_token,
        cancel_flag,
    )

    ephemeral_handle: ExploreIndex | None = None
    try:
        handle: ExploreIndex | None = handlers_module._resolve_explore_index(session)
        if handle is None:
            workspace_root_obj: object = getattr(workspace, "root", None)
            workspace_root_raw: object = workspace_root_obj or params.get("workspace_root", "")
            workspace_root_str: str = str(workspace_root_raw) if workspace_root_raw else ""
            workspace_root = Path(workspace_root_str) if workspace_root_str else Path.cwd()
            handle = handlers_module.build_explore_index(workspace_root)
            ephemeral_handle = handle
        result = run_query(
            handle.store,
            query_type=query_type_raw,
            target=target,
            target_b=target_b,
            relations=relations,
            limit=limit,
            freshness=freshness,
            direction=direction,
            depth=depth,
            max_paths=max_paths,
            change_kind=change_kind,
            scope_path=scope_path,
            role=role,
            deadline=deadline,
            cancel=cancel_callable,
        )
    finally:
        handlers_module._disarm_cancel_flag(
            _GRAPH_CANCEL_FLAGS,
            _GRAPH_CANCEL_LOCK,
            graph_cancel_token,
        )
        if ephemeral_handle is not None:
            with contextlib.suppress(Exception):
                ephemeral_handle.store.close()
    payload = _graph_result_to_dict(result)
    from ralph.mcp.explore.serving import serving_metadata

    payload.update(
        serving_metadata(
            session,
            index_used=ephemeral_handle is None,
            fallback_reason=None if ephemeral_handle is None else "no_committed_generation",
        )
    )
    return ToolResult(
        content=[ToolContent.text_content(_tool_json(payload))],
        is_error=False,
    )
