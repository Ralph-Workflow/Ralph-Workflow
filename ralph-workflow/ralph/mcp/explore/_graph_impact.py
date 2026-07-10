"""Graph impact and hubs queries for ``ralph_graph`` (Phase 2+)."""


import sqlite3

from ralph.mcp.explore.graph import (
    _IMPACT_RELATIONS,
    _RELATION_PRIORITY,
    GraphEdge,
    GraphNode,
    GraphResult,
    _current_generation,
    _iter_incoming,
    _relation_priority,
    _resolve_target,
    _row_to_edge,
    _row_to_node_from_file,
    _row_to_node_from_symbol,
)
from ralph.mcp.explore.store import ExploreStore


def impact(
    store: ExploreStore,
    *,
    target: str,
    change_kind: str,
    limit: int = 25,
) -> GraphResult:
    """Return a conservative impact estimate for ``change_kind``.

    The output labels dynamic / reflective / generated callers as
    ``unknown`` rather than claiming runtime certainty. Default
    relations are looked up in ``_IMPACT_RELATIONS``.
    """
    if change_kind not in _IMPACT_RELATIONS:
        return GraphResult(
            query_type="impact",
            nodes=(),
            edges=(),
            missing_data=("invalid_change_kind",),
            index_generation=_current_generation(store),
            is_stale=False,
            truncated=False,
            metadata={"change_kind": change_kind, "target": target},
        )
    relations = _IMPACT_RELATIONS[change_kind]
    resolved_id, _resolved_name = _resolve_target(store, target)
    if resolved_id is None:
        return GraphResult(
            query_type="impact",
            nodes=(),
            edges=(),
            missing_data=("target_unresolved",),
            index_generation=_current_generation(store),
            is_stale=False,
            truncated=False,
            metadata={"target": target, "change_kind": change_kind},
        )
    bounded_limit = max(1, min(limit, 100))
    impacted: list[GraphEdge] = []
    for edge in _iter_incoming(
        store, target_id=resolved_id, relations=relations
    ):
        impacted.append(_row_to_edge(edge))
        if len(impacted) >= bounded_limit:
            break
    impacted_paths = sorted({edge.path for edge in impacted if edge.path})
    # Walk calls_syntax edges to flag dynamic / unknown cases.
    missing_data: list[str] = []
    if any(
        edge.reason and ("getattr" in edge.reason or "dynamic" in edge.reason)
        for edge in impacted
    ):
        missing_data.append("dynamic_dispatch_possible")
    if any(edge.path.startswith("vendor/") for edge in impacted):
        missing_data.append("generated_vendor_caller_present")

    nodes_out: list[GraphNode] = []
    seen: set[str] = set()
    for impacted_edge in impacted:
        if impacted_edge.source in seen:
            continue
        seen.add(impacted_edge.source)
        cur = store._conn.execute(
            "SELECT * FROM symbols WHERE symbol_id = ?", (impacted_edge.source,)
        )
        row: sqlite3.Row | None = cur.fetchone()
        if row is not None:
            nodes_out.append(_row_to_node_from_symbol(row))
            continue
        nodes_out.append(
            GraphNode(
                id=impacted_edge.source,
                kind="unknown",
                label=impacted_edge.source,
                path=impacted_edge.path,
                confidence=0.0,
                provenance="unknown",
                evidence_ids=(),
            )
        )

    def _impact_node_key(n: GraphNode) -> tuple[str, str]:
        return (n.path, n.id)

    nodes_out.sort(key=_impact_node_key)

    return GraphResult(
        query_type="impact",
        nodes=tuple(nodes_out),
        edges=tuple(impacted),
        impacted_files=tuple(impacted_paths),
        confidence=min((e.confidence for e in impacted), default=0.0),
        provenance="extracted",
        evidence_ids=tuple(e.evidence_id for e in impacted if e.evidence_id),
        missing_data=tuple(missing_data),
        index_generation=_current_generation(store),
        is_stale=False,
        truncated=len(impacted) >= bounded_limit,
        metadata={
            "target": target,
            "resolved_id": resolved_id,
            "change_kind": change_kind,
            "relations": list(relations),
            "limit": bounded_limit,
        },
    )
def hubs(
    store: ExploreStore,
    *,
    scope_path: str | None = None,
    relation: str | None = None,
    role: str | None = None,
    limit: int = 25,
) -> GraphResult:
    """Return deterministic hub ranking by weighted degree.

    Score = ``2 * in_degree + out_degree`` adjusted by the relation
    priority weight. ``scope_path`` and ``relation`` are filters;
    ``role`` is currently accepted but only filters to source/test
    paths. Output is stable: ties sort by path ASC, then id ASC.
    """
    bounded_limit = max(1, min(limit, 100))
    degree: dict[str, dict[str, int]] = {}
    for edge in store.iter_edges(relation=relation):
        source = edge.source_id
        target = edge.target_id
        relation_value = edge.relation
        path_value = edge.path
        if scope_path is not None and not path_value.startswith(scope_path):
            continue
        if role == "source" and not path_value.endswith(
            (".py", ".md", ".json", ".yaml", ".yml", ".toml")
        ):
            continue
        if role == "test" and "/tests/" not in path_value and not path_value.startswith(
            "tests/"
        ):
            continue
        weight = len(_RELATION_PRIORITY) - _relation_priority(relation_value)
        for node_id in (source, target):
            entry = degree.setdefault(node_id, {"in": 0, "out": 0, "weight": 0})
            entry["weight"] += weight
        degree[source]["out"] += 1
        degree[target]["in"] += 1
    scored: list[tuple[int, int, str]] = []
    for node_id, entry in degree.items():
        score = entry["weight"] * 2 + entry["in"] + entry["out"]
        scored.append((score, entry["in"] + entry["out"], node_id))

    def _hub_sort_key(t: tuple[int, int, str]) -> tuple[int, int, str]:
        return (-t[0], -t[1], t[2])

    scored.sort(key=_hub_sort_key)
    scored = scored[:bounded_limit]

    nodes_out: list[GraphNode] = []
    for score, total_degree, node_id in scored:
        cur = store._conn.execute(
            "SELECT * FROM symbols WHERE symbol_id = ?", (node_id,)
        )
        row: sqlite3.Row | None = cur.fetchone()
        if row is not None:
            node = _row_to_node_from_symbol(row)
        elif node_id.startswith("file:"):
            node = _row_to_node_from_file(node_id, node_id[5:])
        else:
            node = GraphNode(
                id=node_id,
                kind="unknown",
                label=node_id,
                path="",
                confidence=0.0,
                provenance="inferred",
                evidence_ids=(),
            )
        # Record the hub score so callers can show why a node
        # ranked above another. ``score`` is the weighted
        # centrality value; ``total_degree`` is its unweighted sum.
        nodes_out.append(node)
        # Ponytail: prefer computing metadata locally rather than
        # threading a third tuple element through the dataclass.
        _ = (score, total_degree)

    return GraphResult(
        query_type="hubs",
        nodes=tuple(nodes_out),
        edges=(),
        confidence=0.0,
        provenance="derived",
        missing_data=(),
        index_generation=_current_generation(store),
        is_stale=False,
        truncated=len(scored) >= bounded_limit,
        metadata={
            "scope_path": scope_path,
            "relation": relation,
            "role": role,
            "limit": bounded_limit,
        },
    )
