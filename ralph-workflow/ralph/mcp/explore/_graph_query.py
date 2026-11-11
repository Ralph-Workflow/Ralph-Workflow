
from collections.abc import Callable, Sequence

from ralph.mcp.explore._graph_impact import hubs as _hubs
from ralph.mcp.explore._graph_impact import impact as _impact
from ralph.mcp.explore._graph_neighbors import neighbors as _neighbors
from ralph.mcp.explore._graph_path import path_query as _path_query
from ralph.mcp.explore._graph_tests import tests_for as _tests_for
from ralph.mcp.explore.graph import (
    GraphResult,
    _cancelled_result,
    _current_generation,
    _deadline_elapsed,
    _deadline_result,
    _is_cancelled,
)
from ralph.mcp.explore.store import ExploreStore


def run_query(
    store: ExploreStore,
    *,
    query_type: str,
    target: str,
    target_b: str | None = None,
    relations: Sequence[str] | None = None,
    limit: int = 25,
    freshness: str = "prefer_fresh",
    direction: str = "both",
    depth: int = 1,
    max_paths: int = 3,
    change_kind: str = "unknown",
    scope_path: str | None = None,
    role: str | None = None,
    deadline: float | None = None,
    cancel: Callable[[], bool] | None = None,
) -> GraphResult:
    dirty = list(store.peek_dirty_paths())
    is_stale = bool(dirty)
    if _is_cancelled(cancel):
        return _cancelled_result(query_type, target, target_b, store)
    if _deadline_elapsed(deadline):
        return _deadline_result(query_type, target, target_b, store)
    if freshness == "required" and dirty:
        return GraphResult(
            query_type=query_type,
            nodes=(),
            edges=(),
            paths=(),
            missing_data=("freshness_required",),
            index_generation=_current_generation(store),
            is_stale=True,
            truncated=False,
            metadata={
                "target": target,
                "target_b": target_b,
                "dirty_paths_count": len(dirty),
                "freshness": freshness,
            },
        )
    if query_type == "neighbors":
        if _is_cancelled(cancel):
            return _cancelled_result(query_type, target, target_b, store)
        if _deadline_elapsed(deadline):
            return _deadline_result(query_type, target, target_b, store)
        result = _neighbors(
            store,
            target=target,
            relations=relations,
            direction=direction,
            depth=depth,
            limit=limit,
        )
    elif query_type == "path":
        if not target_b:
            return GraphResult(
                query_type="path",
                nodes=(),
                edges=(),
                paths=(),
                missing_data=("target_b_required",),
                index_generation=_current_generation(store),
                is_stale=is_stale,
                truncated=False,
            )
        if _is_cancelled(cancel):
            return _cancelled_result(query_type, target, target_b, store)
        if _deadline_elapsed(deadline):
            return _deadline_result(query_type, target, target_b, store)
        result = _path_query(
            store,
            target=target,
            target_b=target_b,
            relations=relations,
            max_paths=max_paths,
            depth=depth,
            limit=limit,
        )
    elif query_type == "impact":
        if _is_cancelled(cancel):
            return _cancelled_result(query_type, target, target_b, store)
        if _deadline_elapsed(deadline):
            return _deadline_result(query_type, target, target_b, store)
        result = _impact(store, target=target, change_kind=change_kind, limit=limit)
    elif query_type == "hubs":
        if _is_cancelled(cancel):
            return _cancelled_result(query_type, target, target_b, store)
        if _deadline_elapsed(deadline):
            return _deadline_result(query_type, target, target_b, store)
        result = _hubs(
            store,
            scope_path=scope_path,
            relation=relations[0] if relations else None,
            role=role,
            limit=limit,
        )
    elif query_type == "tests":
        if _is_cancelled(cancel):
            return _cancelled_result(query_type, target, target_b, store)
        if _deadline_elapsed(deadline):
            return _deadline_result(query_type, target, target_b, store)
        result = _tests_for(store, target=target, limit=limit)
    else:
        return GraphResult(
            query_type=query_type,
            nodes=(),
            edges=(),
            missing_data=("unsupported_query_type",),
            index_generation=_current_generation(store),
            is_stale=is_stale,
            truncated=False,
        )
    if freshness != "allow_stale":
        result = result.with_staleness(is_stale=is_stale)
    return result
