"""MCP handler for ``ralph_graph``.

Extracted from :mod:`ralph.mcp.explore.handlers` so the hub module
stays under the repository's per-file line ceiling. The handler is
the only public surface; the helpers (``_graph_node_to_dict``,
``_graph_edge_to_dict``, ``_graph_result_to_dict``) are implementation
detail and remain importable from this module for test reach.

Cancel-token wiring uses the shared ``_new_cancel_token`` /
``_arm_cancel_flag`` / ``_disarm_cancel_flag`` helpers in
:mod:`ralph.mcp.explore.handlers` so both the reindex and graph
handlers share one source of truth.
"""

from __future__ import annotations

import contextlib
import threading
import time
from collections.abc import Callable
from pathlib import Path

from ralph.mcp.explore import graph as graph_module
from ralph.mcp.explore import handlers as handlers_module
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

# --- ralph_graph handler (AC-07) ------------------------------------------


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
#: Prompt-exact upper bound for the ``limit`` parameter (the graph
#: contract always exposes the same cap).
_GRAPH_LIMIT_MAX: int = 100
#: Default per-call budget for ``ralph_graph`` queries, in
#: milliseconds. Picked to match the existing reindex default
#: (5 s) so a single tool call does not silently outlive its
#: expected budget.
_GRAPH_DEFAULT_TIMEOUT_MS: int = 5_000
#: Maximum permissible ``timeout_ms`` for ``ralph_graph``. The
#: handler rejects any value outside ``[1, _GRAPH_TIMEOUT_MAX_MS]``
#: so callers cannot extend the budget arbitrarily. Matches the
#: default in the schema (1-30000).
_GRAPH_TIMEOUT_MAX_MS: int = 30_000
#: Cooperative-cancellation flag registry. The handler stores
#: ``True`` when the caller asked to cancel, keyed by a
#: per-request token (UUID). The dispatcher polls the flag at
#: phase boundaries. The token is generated at the start of
#: every call and removed in the ``finally`` block, so a
#: previous caller's cancel cannot poison a concurrent query
#: against the same session and the map cannot leak across
#: long-lived sessions.
#:
#: Ponytail: each entry is keyed by a unique token that lives
#: only for the duration of one call. Concurrent calls against
#: the same session get distinct tokens and never observe each
#: other's flags. The internal lock guards concurrent mutation
#: of the dict so a writer that arms its flag and a cleanup
#: path that pops a different call's flag cannot race.
_GRAPH_CANCEL_FLAGS: dict[str, bool] = {}  # bounded-accumulator-ok: keyed by request token; one entry per active call
_GRAPH_CANCEL_LOCK: threading.Lock = threading.Lock()

#: Per-request cancel flag for ralph_reindex. Mirrors the
#: ``_GRAPH_CANCEL_FLAGS`` contract — one entry per active call,
#: keyed by a unique request token, cleared on every exit path.
#: The reindex writer polls this flag at phase boundaries; when
#: set, the writer preserves the prior committed generation and
#: returns a ``cancelled`` result. Concurrent reindex calls
#: against the same session get distinct tokens.
_REINDEX_CANCEL_FLAGS: dict[str, bool] = {}  # bounded-accumulator-ok: keyed by request token; one entry per active call
_REINDEX_CANCEL_LOCK: threading.Lock = threading.Lock()


# Local alias for the imported cancel-token factory. The canonical
# implementation lives in ``handlers.py`` so both the reindex and
# graph handlers share one source of truth; the alias is purely
# cosmetic for readability at the call sites below.
_new_cancel_token_ = _new_cancel_token


def _arm_cancel_flag(
    registry: dict[str, bool],
    lock: threading.Lock,
    token: str,
    initial: bool,
) -> Callable[[], bool]:
    """Register a per-request cancel flag and return its poll callable.

    The token is unique to this call. A concurrent call against
    the same session generates a different token, so concurrent
    callers cannot observe or mutate each other's flags. The
    returned callable reads the flag under ``lock`` so a writer
    that flips the flag does not race with the dispatcher's
    poll. Removal happens in the ``finally`` block via
    ``_disarm_cancel_flag``.
    """
    with lock:
        registry[token] = bool(initial)

    def _is_set() -> bool:
        with lock:
            return bool(registry.get(token, False))

    return _is_set


def _disarm_cancel_flag(
    registry: dict[str, bool],
    lock: threading.Lock,
    token: str,
) -> None:
    """Remove the per-request cancel flag entry on every exit path.

    The token-based key guarantees the pop never deletes a
    concurrent caller's flag. The lock prevents the pop from
    racing with a poll that has already loaded the entry.
    """
    with lock:
        registry.pop(token, None)


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
    """Bounded graph-native query over the indexed exploration substrate.

    Capability: ``WorkspaceRead``. Every response includes the
    prompt-exact shared fields (``nodes``, ``edges``, ``paths``,
    ``impacted_files``, ``suggested_tests``, ``confidence``,
    ``provenance``, ``evidence_ids``, ``missing_data``,
    ``index_generation``, ``is_stale``, ``truncated``,
    ``cancelled``, ``deadline_exceeded``).

    AC-05: ``timeout_ms`` (1-30000) is a bounded per-call budget.
    On deadline expiry the dispatcher returns a bounded, truthful
    incomplete result (``deadline_exceeded=true``,
    ``missing_data=("deadline_exceeded",)``) without exposing
    mutable work. ``cancel=true`` requests cooperative cancellation
    with the same bounded contract (``cancelled=true``,
    ``missing_data=("cancelled",)``).
    """
    require_capability(session, WORKSPACE_READ_CAPABILITY, "Graph query")
    query_type_raw: object = params.get("query_type", "")
    if not isinstance(query_type_raw, str) or query_type_raw not in _VALID_GRAPH_QUERY_TYPES:
        raise InvalidParamsError(
            f"Invalid query_type: {query_type_raw!r}; expected one of "
            f"{', '.join(_VALID_GRAPH_QUERY_TYPES)}"
        )
    target = str(params.get("target", "")) if params.get("target") is not None else ""
    # AC-05: the documented contract is that ``target`` is required for
    # ``neighbors`` / ``path`` / ``impact`` / ``tests`` and optional only
    # for ``hubs``. A targetless request against the four required-target
    # query types would otherwise run a degenerate traversal that returns
    # no evidence; failing closed at the boundary prevents callers from
    # silently relying on an undocumented ``empty-target`` fallback.
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
    freshness: str = (
        str(freshness_raw) if isinstance(freshness_raw, str) else "prefer_fresh"
    )
    if freshness not in _VALID_FRESHNESS:
        raise InvalidParamsError(
            f"Invalid freshness: {freshness!r}; expected one of "
            f"{', '.join(_VALID_FRESHNESS)}"
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
    change_kind: str = (
        str(change_kind_raw) if isinstance(change_kind_raw, str) else "unknown"
    )
    if change_kind not in _VALID_CHANGE_KINDS:
        raise InvalidParamsError(
            f"Invalid change_kind: {change_kind!r}; expected one of "
            f"{', '.join(_VALID_CHANGE_KINDS)}"
        )
    scope_path_raw = params.get("scope_path")
    scope_path: str | None = (
        str(scope_path_raw)
        if isinstance(scope_path_raw, str) and scope_path_raw
        else None
    )
    role_raw = params.get("role")
    role: str | None = (
        str(role_raw) if isinstance(role_raw, str) and role_raw else None
    )
    # AC-05: bounded per-call deadline. Reject malformed values
    # fail-closed; only positive integers in [1, 30000] are
    # accepted. The deadline is converted to a monotonic-clock
    # absolute deadline so a future system-clock change cannot
    # extend the budget.
    timeout_ms = _strict_int_param(
        params,
        "timeout_ms",
        default=_GRAPH_DEFAULT_TIMEOUT_MS,
        min_value=1,
        max_value=_GRAPH_TIMEOUT_MAX_MS,
    )
    deadline = time.monotonic() + timeout_ms / 1000.0
    # AC-05: cooperative cancellation. ``cancel=true`` flips the
    # per-request flag to True; the dispatcher polls it at phase
    # boundaries. The flag is keyed by a fresh per-request token
    # so a previous caller's cancel cannot poison a new query and
    # concurrent queries against the same session get distinct
    # tokens that cannot observe each other.
    cancel_raw: object = params.get("cancel", False)
    cancel_flag = bool(cancel_raw) if isinstance(cancel_raw, bool) else False
    # AC-05: cooperative cancellation. The flag is registered
    # under a fresh per-request token so concurrent queries
    # against the same session get distinct entries; one caller's
    # cancel never cancels or clears another caller's flag. The
    # dispatcher polls the flag at phase boundaries. The entry is
    # explicitly removed in the ``finally`` block so repeated
    # long-lived sessions do not leak entries into the
    # module-global map.
    graph_cancel_token = _new_cancel_token()
    cancel_callable: Callable[[], bool] = handlers_module._arm_cancel_flag(
        _GRAPH_CANCEL_FLAGS,
        _GRAPH_CANCEL_LOCK,
        graph_cancel_token,
        cancel_flag,
    )

    # AC-02/AC-05: track whether the graph call lazily built an
    # ephemeral index that no caller is responsible for closing.
    # The finally block closes the underlying SQLite store so the
    # per-call file handle is released before the next call.
    ephemeral_handle: ExploreIndex | None = None
    try:
        handle: ExploreIndex | None = handlers_module._resolve_explore_index(session)
        if handle is None:
            workspace_root_obj: object = getattr(workspace, "root", None)
            workspace_root_raw: object = workspace_root_obj or params.get(
                "workspace_root", ""
            )
            workspace_root_str: str = (
                str(workspace_root_raw) if workspace_root_raw else ""
            )
            workspace_root = (
                Path(workspace_root_str)
                if workspace_root_str
                else Path.cwd()
            )
            handle = handlers_module.build_explore_index(workspace_root)
            ephemeral_handle = handle
        # ``graph.run_query`` is dynamically exported via a module-level
        # ``__getattr__`` (PEP 562) so mypy sees the call as ``Any``.
        # The runtime resolution is data-driven and pinned by the
        # ``graph._LAZY_REEXPORTS`` table; the suppression is
        # scaffolded by the PEP 562 dispatcher and not by an
        # autogenerated parser.
        result = graph_module.run_query(  # type: ignore[operator,misc]  # reason: autogenerated code has no type support, see docs/agents/type-ignore-policy.md#autogenerated-code
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
        # AC-02/AC-05: bounded accumulator contract. The cancel
        # flag is scoped to a unique per-request token, not the
        # session lifetime, so the pop never deletes a concurrent
        # caller's flag. Concurrent queries against the same
        # session are isolated by their distinct tokens.
        handlers_module._disarm_cancel_flag(
            _GRAPH_CANCEL_FLAGS,
            _GRAPH_CANCEL_LOCK,
            graph_cancel_token,
        )
        # AC-05: ephemeral store cleanup. When the call lazily
        # built a fresh index, close the underlying SQLite store
        # so file handles do not accumulate across calls.
        if ephemeral_handle is not None:
            with contextlib.suppress(Exception):
                ephemeral_handle.store.close()
    payload = _graph_result_to_dict(result)  # type: ignore[misc]  # reason: autogenerated code has no type support, see docs/agents/type-ignore-policy.md#autogenerated-code
    return ToolResult(
        content=[ToolContent.text_content(_tool_json(payload))],
        is_error=False,
    )

