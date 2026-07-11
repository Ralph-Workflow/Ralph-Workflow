"""Graph path query for ``ralph_graph`` (Phase 2+)."""


import sqlite3
from collections.abc import Sequence

from ralph.mcp.explore.graph import (
    _RELATION_PRIORITY,
    GraphEdge,
    GraphNode,
    GraphResult,
    _current_generation,
    _iter_outgoing,
    _resolve_target,
    _row_to_edge,
    _row_to_node_from_file,
    _row_to_node_from_symbol,
)
from ralph.mcp.explore.store import ExploreStore


def path_query(
    store: ExploreStore,
    *,
    target: str,
    target_b: str,
    relations: Sequence[str] | None = None,
    max_paths: int = 3,
    depth: int = 4,
    limit: int = 25,
) -> GraphResult:
    """Bounded shortest-path search between two endpoints.

    Returns ``missing_data=("endpoint_unresolved",)`` when either
    endpoint cannot be resolved. Paths sort by length, then minimum
    edge confidence, then lexicographic path string for stable output.
    """
    relations_actual = tuple(relations) if relations else _RELATION_PRIORITY
    a_id, _a_name = _resolve_target(store, target)
    b_id, _b_name = _resolve_target(store, target_b)
    if a_id is None or b_id is None:
        return GraphResult(
            query_type="path",
            nodes=(),
            edges=(),
            paths=(),
            missing_data=("endpoint_unresolved",),
            index_generation=_current_generation(store),
            is_stale=False,
            truncated=False,
            metadata={"target": target, "target_b": target_b},
        )
    bounded_depth = max(1, min(depth, 6))
    bounded_max_paths = max(1, min(max_paths, 10))
    bounded_limit = max(1, min(limit, 100))

    # BFS forward from a_id, recording path.
    queue: list[tuple[str, list[str], list[GraphEdge]]] = [(a_id, [a_id], [])]
    visited: set[str] = {a_id}
    found: list[tuple[list[str], list[GraphEdge]]] = []
    truncated = False
    while queue and len(found) < bounded_max_paths:
        current_id, current_path, current_edges = queue.pop(0)
        if current_id == b_id:
            found.append((current_path, current_edges))
            continue
        if len(current_path) >= bounded_depth + 1:
            continue
        for edge in _iter_outgoing(
            store, source_id=current_id, relations=relations_actual
        ):
            if edge.target_id in visited:
                continue
            visited.add(edge.target_id)
            queue.append(
                (
                    edge.target_id,
                    [*current_path, edge.target_id],
                    [*current_edges, _row_to_edge(edge)],
                )
            )
            if len(visited) >= bounded_limit:
                truncated = True
                break
        if len(visited) >= bounded_limit:
            truncated = True
            break
    if not found:
        return GraphResult(
            query_type="path",
            nodes=(),
            edges=(),
            paths=(),
            missing_data=("no_path",),
            index_generation=_current_generation(store),
            is_stale=False,
            truncated=truncated,
            metadata={"target": target, "target_b": target_b},
        )

    def _path_key(item: tuple[list[str], list[GraphEdge]]) -> tuple[int, float, str]:
        path_nodes, path_edges = item
        return (
            len(path_nodes),
            -min((e.confidence for e in path_edges), default=0.0),
            ",".join(path_nodes),
        )

    found.sort(key=_path_key)
    found = found[:bounded_max_paths]

    node_ids: set[str] = set()
    edges_out: list[GraphEdge] = []
    path_strings: list[tuple[str, ...]] = []
    for path_nodes, path_edges in found:
        path_strings.append(tuple(path_nodes))
        node_ids.update(path_nodes)
        edges_out.extend(path_edges)

    nodes_out: list[GraphNode] = []
    for node_id in node_ids:
        cur = store._conn.execute(
            "SELECT * FROM symbols WHERE symbol_id = ?", (node_id,)
        )
        row: sqlite3.Row | None = cur.fetchone()
        if row is not None:
            nodes_out.append(_row_to_node_from_symbol(row))
            continue
        if node_id.startswith("file:"):
            nodes_out.append(_row_to_node_from_file(node_id, node_id[5:]))
            continue
        nodes_out.append(
            GraphNode(
                id=node_id,
                kind="unresolved",
                label=node_id,
                path="",
                confidence=0.5,
                provenance="inferred",
                evidence_ids=(),
            )
        )

    def _node_id_key(n: GraphNode) -> tuple[str, str]:
        return (n.path, n.id)

    nodes_out.sort(key=_node_id_key)

    return GraphResult(
        query_type="path",
        nodes=tuple(nodes_out),
        edges=tuple(edges_out),
        paths=tuple(path_strings),
        confidence=min(
            (e.confidence for edges_list in (path_edges for _, path_edges in found) for e in edges_list),
            default=0.0,
        ),
        provenance="extracted",
        evidence_ids=tuple(e.evidence_id for e in edges_out if e.evidence_id),
        missing_data=(),
        index_generation=_current_generation(store),
        is_stale=False,
        truncated=truncated,
        metadata={
            "target": target,
            "target_b": target_b,
            "resolved_a": a_id,
            "resolved_b": b_id,
            "max_paths": bounded_max_paths,
            "depth": bounded_depth,
        },
    )
