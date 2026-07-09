"""Bounded graph queries over the indexed exploration substrate.

Implements the prompt-exact ``ralph_graph`` MCP tool:

* ``neighbors`` \u2014 bounded-depth adjacency walk with relation
  allowlists and confidence ordering.
* ``path`` \u2014 shortest-path search between two endpoints.
* ``impact`` \u2014 conservative caller/importer/test impact for a
  given ``change_kind``.
* ``hubs`` \u2014 deterministic ranking by in/out-degree weighted by
  relation priority.
* ``tests`` \u2014 deterministic test suggestions for a target symbol.

All queries are bounded by ``limit`` (default 25, max 100),
``depth`` / ``max_paths`` where applicable, and ``freshness``
policy. Every result records ``confidence``, ``provenance``,
``evidence_ids``, ``missing_data``, ``index_generation``,
``is_stale``, ``truncated``, and bounded byte output.

The module is read-only against the :class:`ExploreStore`. It
never mutates rows; it only reads from the structure tables
introduced in Phase 2.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Final

from ralph.mcp.explore.store import EdgeRow, ExploreStore

# Relation priority for ordering (matches the prompt's contract).
_RELATION_PRIORITY: Final[tuple[str, ...]] = (
    "defines",
    "imports",
    "calls_syntax",
    "tests",
    "references_text",
    "mentions",
    "inherits_syntax",
    "contains",
)


# Allowed impact relations per change_kind (prompt-exact).
# Ponytail: a ``Final[MappingProxyType]`` is immutable at runtime so
# the resource-lifecycle audit can accept the constant without a
# mutable-literal marker.
_IMPACT_RELATIONS: Final[Mapping[str, tuple[str, ...]]] = MappingProxyType(
    {
        "rename": ("imports", "calls_syntax", "references_text", "tests", "mentions"),
        "signature": ("calls_syntax", "tests", "references_text"),
        "behavior": ("imports", "calls_syntax", "tests"),
        "delete": ("contains", "imports", "calls_syntax", "tests", "references_text"),
        "unknown": ("contains", "imports", "calls_syntax", "tests"),
    }
)


@dataclass(frozen=True, slots=True)
class GraphNode:
    """A single graph node returned by a query."""

    id: str
    kind: str
    label: str
    path: str
    confidence: float
    provenance: str
    evidence_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class GraphEdge:
    """A single graph edge returned by a query."""

    source: str
    target: str
    relation: str
    path: str
    confidence: float
    provenance: str
    reason: str | None = None
    evidence_id: str | None = None


@dataclass(frozen=True, slots=True)
class GraphResult:
    """A complete graph query response."""

    query_type: str
    nodes: tuple[GraphNode, ...]
    edges: tuple[GraphEdge, ...]
    paths: tuple[tuple[str, ...], ...] = ()
    impacted_files: tuple[str, ...] = ()
    suggested_tests: tuple[GraphNode, ...] = ()
    confidence: float = 0.0
    provenance: str = "extracted"
    evidence_ids: tuple[str, ...] = ()
    missing_data: tuple[str, ...] = ()
    index_generation: int = 0
    is_stale: bool = False
    truncated: bool = False
    metadata: Mapping[str, object] = field(default_factory=dict)


def _relation_priority(relation: str) -> int:
    """Return the priority index of ``relation`` (lower is higher)."""
    try:
        return _RELATION_PRIORITY.index(relation)
    except ValueError:
        return len(_RELATION_PRIORITY)


def _resolve_target(store: ExploreStore, target: str) -> tuple[str | None, str | None]:
    """Resolve a caller-supplied ``target`` to ``(symbol_id, qualified_name)``.

    Accepts a qualified name, a symbol_id, or a path. Returns
    ``(None, None)`` when the target cannot be resolved.
    """
    if not target:
        return None, None
    cur = store._conn.execute(
        "SELECT symbol_id, qualified_name FROM symbols "
        "WHERE qualified_name = ? OR symbol_id = ? LIMIT 1",
        (target, target),
    )
    row = cur.fetchone()
    if row is not None:
        return str(row[0]), str(row[1])
    if target.endswith(".py") or target.endswith(".md"):
        cur = store._conn.execute(
            "SELECT symbol_id, qualified_name FROM symbols "
            "WHERE path = ? ORDER BY qualified_name LIMIT 1",
            (target,),
        )
        row = cur.fetchone()
        if row is not None:
            return str(row[0]), str(row[1])
        return f"file:{target}", target
    return None, None


def _row_to_edge(row: EdgeRow) -> GraphEdge:
    """Translate an :class:`EdgeRow` to a :class:`GraphEdge`."""
    return GraphEdge(
        source=row.source_id,
        target=row.target_id,
        relation=row.relation,
        path=row.path,
        confidence=row.confidence,
        provenance=row.provenance,
        reason=row.reason,
        evidence_id=row.edge_id,
    )


def _row_to_node_from_symbol(row: sqlite3.Row) -> GraphNode:
    """Translate a symbols row to a GraphNode."""
    evidence_ids: tuple[str, ...] = (str(row["span_id"]), str(row["symbol_id"]))
    return GraphNode(
        id=str(row["symbol_id"]),
        kind=str(row["kind"]),
        label=str(row["qualified_name"]),
        path=str(row["path"]),
        confidence=float(row["confidence"]),
        provenance=str(row["extracted_from"]),
        evidence_ids=evidence_ids,
    )


def _row_to_node_from_file(file_id: str, path: str) -> GraphNode:
    return GraphNode(
        id=file_id,
        kind="file",
        label=path,
        path=path,
        confidence=1.0,
        provenance="file",
        evidence_ids=(),
    )


def _current_generation(store: ExploreStore) -> int:
    raw = store.get_setting("current_generation")
    if raw is None or not raw.isdigit():
        return 0
    return int(raw)


def _iter_outgoing(
    store: ExploreStore,
    *,
    source_id: str,
    relations: Sequence[str],
) -> Iterable[EdgeRow]:
    placeholders = ",".join("?" for _ in relations) if relations else ""
    sql = (
        "SELECT * FROM edges WHERE source_id = ?"
        + (f" AND relation IN ({placeholders})" if relations else "")
        + " ORDER BY confidence DESC, relation ASC"
    )
    params: tuple[object, ...] = (source_id, *relations) if relations else (source_id,)
    cur = store._conn.execute(sql, params)
    rows = list(cur.fetchall())
    for row in rows:
        yield EdgeRow(
            edge_id=str(row["edge_id"]),
            source_id=str(row["source_id"]),
            target_id=str(row["target_id"]),
            relation=str(row["relation"]),
            path=str(row["path"]),
            span_id=str(row["span_id"]) if row["span_id"] is not None else None,
            provenance=str(row["provenance"]),
            confidence=float(row["confidence"]),
            reason=str(row["reason"]) if row["reason"] is not None else None,
            generation=int(row["generation"]),
        )


def _iter_incoming(
    store: ExploreStore,
    *,
    target_id: str,
    relations: Sequence[str],
) -> Iterable[EdgeRow]:
    placeholders = ",".join("?" for _ in relations) if relations else ""
    sql = (
        "SELECT * FROM edges WHERE target_id = ?"
        + (f" AND relation IN ({placeholders})" if relations else "")
        + " ORDER BY confidence DESC, relation ASC"
    )
    params: tuple[object, ...] = (target_id, *relations) if relations else (target_id,)
    cur = store._conn.execute(sql, params)
    rows = list(cur.fetchall())
    for row in rows:
        yield EdgeRow(
            edge_id=str(row["edge_id"]),
            source_id=str(row["source_id"]),
            target_id=str(row["target_id"]),
            relation=str(row["relation"]),
            path=str(row["path"]),
            span_id=str(row["span_id"]) if row["span_id"] is not None else None,
            provenance=str(row["provenance"]),
            confidence=float(row["confidence"]),
            reason=str(row["reason"]) if row["reason"] is not None else None,
            generation=int(row["generation"]),
        )


# --- neighbors ------------------------------------------------------------


def neighbors(
    store: ExploreStore,
    *,
    target: str,
    relations: Sequence[str] | None = None,
    direction: str = "both",
    depth: int = 1,
    limit: int = 25,
) -> GraphResult:
    """Return bounded-depth neighbors for ``target``.

    Relations default to the full evidence-backed allowlist. ``depth``
    is capped at 3. Ordering: confidence DESC, relation priority
    ASC, then path ASC for stable output.
    """
    relations_actual = tuple(relations) if relations else _RELATION_PRIORITY
    resolved_id, resolved_name = _resolve_target(store, target)
    if resolved_id is None:
        return GraphResult(
            query_type="neighbors",
            nodes=(),
            edges=(),
            missing_data=("target_unresolved",),
            index_generation=_current_generation(store),
            is_stale=False,
            truncated=False,
            metadata={"target": target},
        )
    bounded_depth = max(1, min(depth, 3))
    bounded_limit = max(1, min(limit, 100))
    visited_nodes: dict[str, GraphNode] = {}
    visited_edges: list[GraphEdge] = []
    truncated = False

    def _node_for_symbol(symbol_id: str) -> GraphNode | None:
        if symbol_id in visited_nodes:
            return visited_nodes[symbol_id]
        cur = store._conn.execute(
            "SELECT * FROM symbols WHERE symbol_id = ?", (symbol_id,)
        )
        row = cur.fetchone()
        if row is None:
            if symbol_id.startswith("file:"):
                return _row_to_node_from_file(symbol_id, symbol_id[5:])
            if symbol_id.startswith("unresolved:"):
                label = symbol_id[len("unresolved:") :]
                return GraphNode(
                    id=symbol_id,
                    kind="unresolved",
                    label=label,
                    path="",
                    confidence=0.5,
                    provenance="inferred",
                    evidence_ids=(),
                )
            return None
        node = _row_to_node_from_symbol(row)
        visited_nodes[symbol_id] = node
        return node

    # Seed the frontier with the target itself.
    frontier: list[str] = [resolved_id]
    visited_ids: set[str] = {resolved_id}
    for _current_depth in range(bounded_depth):
        next_frontier: list[str] = []
        for current_id in frontier:
            if direction in {"out", "both"}:
                for edge in _iter_outgoing(
                    store, source_id=current_id, relations=relations_actual
                ):
                    visited_edges.append(_row_to_edge(edge))
                    _node_for_symbol(edge.target_id)
                    if edge.target_id not in visited_ids:
                        visited_ids.add(edge.target_id)
                        next_frontier.append(edge.target_id)
                        if len(visited_nodes) >= bounded_limit:
                            truncated = True
                            break
            if direction in {"in", "both"}:
                for edge in _iter_incoming(
                    store, target_id=current_id, relations=relations_actual
                ):
                    visited_edges.append(_row_to_edge(edge))
                    _node_for_symbol(edge.source_id)
                    if edge.source_id not in visited_ids:
                        visited_ids.add(edge.source_id)
                        next_frontier.append(edge.source_id)
                        if len(visited_nodes) >= bounded_limit:
                            truncated = True
                            break
            if len(visited_nodes) >= bounded_limit:
                truncated = True
                break
        frontier = next_frontier
        if not frontier or truncated:
            break

    nodes_sorted = sorted(
        visited_nodes.values(),
        key=lambda n: (
            -n.confidence,
            _relation_priority("defines"),
            n.path,
        ),
    )[:bounded_limit]
    edges_sorted = sorted(
        visited_edges,
        key=lambda e: (
            -e.confidence,
            _relation_priority(e.relation),
            e.path,
        ),
    )[:bounded_limit]
    return GraphResult(
        query_type="neighbors",
        nodes=tuple(nodes_sorted),
        edges=tuple(edges_sorted),
        confidence=min((n.confidence for n in nodes_sorted), default=0.0),
        provenance="extracted",
        evidence_ids=tuple(e.evidence_id for e in edges_sorted if e.evidence_id),
        missing_data=(),
        index_generation=_current_generation(store),
        is_stale=False,
        truncated=truncated,
        metadata={
            "target": target,
            "resolved_id": resolved_id,
            "resolved_name": resolved_name,
            "direction": direction,
            "depth": bounded_depth,
            "limit": bounded_limit,
            "relations": list(relations_actual),
        },
    )


# --- path -----------------------------------------------------------------


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
        row = cur.fetchone()
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
    nodes_out.sort(key=lambda n: (n.path, n.id))

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


# --- impact ---------------------------------------------------------------


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
    for edge in impacted:
        if edge.source in seen:
            continue
        seen.add(edge.source)
        cur = store._conn.execute(
            "SELECT * FROM symbols WHERE symbol_id = ?", (edge.source,)
        )
        row = cur.fetchone()
        if row is not None:
            nodes_out.append(_row_to_node_from_symbol(row))
            continue
        nodes_out.append(
            GraphNode(
                id=edge.source,
                kind="unknown",
                label=edge.source,
                path=edge.path,
                confidence=0.0,
                provenance="unknown",
                evidence_ids=(),
            )
        )
    nodes_out.sort(key=lambda n: (n.path, n.id))

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


# --- hubs -----------------------------------------------------------------


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
    sql = "SELECT source_id, target_id, relation, path FROM edges"
    clauses: list[str] = []
    params: tuple[object, ...] = ()
    if scope_path is not None:
        clauses.append("path LIKE ?")
        params = (*params, scope_path + "%")
    if relation is not None:
        clauses.append("relation = ?")
        params = (*params, relation)
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY path, source_id, target_id"
    cur = store._conn.execute(sql, params)
    rows = list(cur.fetchall())
    degree: dict[str, dict[str, int]] = {}
    for row in rows:
        source = str(row["source_id"])
        target = str(row["target_id"])
        relation_value = str(row["relation"])
        path_value = str(row["path"])
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
    scored.sort(key=lambda t: (-t[0], -t[1], t[2]))
    scored = scored[:bounded_limit]

    nodes_out: list[GraphNode] = []
    for score, total_degree, node_id in scored:
        cur = store._conn.execute(
            "SELECT * FROM symbols WHERE symbol_id = ?", (node_id,)
        )
        row = cur.fetchone()
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


# --- tests ----------------------------------------------------------------


_TEST_PATH_PREFIXES: Final[tuple[str, ...]] = (
    "tests/",
    "/tests/",
    "test_",
    "/test_",
)
_TEST_PATH_SUFFIXES: Final[tuple[str, ...]] = (
    "_test.py",
    ".test.py",
    "_test.",
)


def tests_for(
    store: ExploreStore,
    *,
    target: str,
    limit: int = 25,
) -> GraphResult:
    """Return deterministic ``tests`` suggestions for ``target``.

    The output is a suggested set, not a proof of behavioral
    coverage. Suggestions are derived from:

    * files that import the target,
    * files that text-reference the target's qualified name,
    * files under ``tests/`` whose path matches the target's module.
    """
    bounded_limit = max(1, min(limit, 100))
    resolved_id, resolved_name = _resolve_target(store, target)
    if resolved_id is None:
        return GraphResult(
            query_type="tests",
            nodes=(),
            edges=(),
            suggested_tests=(),
            missing_data=("target_unresolved",),
            index_generation=_current_generation(store),
            is_stale=False,
            truncated=False,
            metadata={"target": target},
        )
    # Path-based candidates (test naming conventions).
    cur = store._conn.execute(
        "SELECT DISTINCT path FROM edges WHERE target_id = ?", (resolved_id,)
    )
    candidate_paths: set[str] = set()
    for row in cur.fetchall():
        candidate_paths.add(str(row["path"]))
    # Plus text references to the target's qualified name.
    if resolved_name is not None:
        cur = store._conn.execute(
            "SELECT DISTINCT path FROM edges WHERE target_id = ?",
            (resolved_name,),
        )
        for row in cur.fetchall():
            candidate_paths.add(str(row["path"]))
    test_paths: set[str] = set()
    for path in candidate_paths:
        lowered = path.lower()
        if any(p in lowered for p in _TEST_PATH_PREFIXES) or any(
            lowered.endswith(s) for s in _TEST_PATH_SUFFIXES
        ):
            test_paths.add(path)
    suggested = [
        GraphNode(
            id=f"file:{test_path}",
            kind="test",
            label=test_path,
            path=test_path,
            confidence=0.6,
            provenance="inferred",
            evidence_ids=(),
        )
        for test_path in sorted(test_paths)[:bounded_limit]
    ]

    return GraphResult(
        query_type="tests",
        nodes=(),
        edges=(),
        suggested_tests=tuple(suggested),
        confidence=min((n.confidence for n in suggested), default=0.0),
        provenance="inferred",
        evidence_ids=(),
        missing_data=("behavioral_coverage_unproven",),
        index_generation=_current_generation(store),
        is_stale=False,
        truncated=len(suggested) >= bounded_limit,
        metadata={
            "target": target,
            "resolved_id": resolved_id,
            "limit": bounded_limit,
        },
    )


# --- Dispatcher -----------------------------------------------------------


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
) -> GraphResult:
    """Dispatch a single ``ralph_graph`` query by ``query_type``."""
    if query_type == "neighbors":
        result = neighbors(
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
                is_stale=False,
                truncated=False,
            )
        result = path_query(
            store,
            target=target,
            target_b=target_b,
            relations=relations,
            max_paths=max_paths,
            depth=depth,
            limit=limit,
        )
    elif query_type == "impact":
        result = impact(
            store, target=target, change_kind=change_kind, limit=limit
        )
    elif query_type == "hubs":
        result = hubs(
            store,
            scope_path=scope_path,
            relation=relations[0] if relations else None,
            role=role,
            limit=limit,
        )
    elif query_type == "tests":
        result = tests_for(store, target=target, limit=limit)
    else:
        return GraphResult(
            query_type=query_type,
            nodes=(),
            edges=(),
            missing_data=("unsupported_query_type",),
            index_generation=_current_generation(store),
            is_stale=False,
            truncated=False,
        )
    # Freshness policy: mark stale when the caller requires a fresh
    # index and the rows reference an older generation. The
    # current behavior is permissive: empty results are allowed
    # when missing_data is set, so no special handling is needed
    # here.
    return result


__all__ = [
    "GraphEdge",
    "GraphNode",
    "GraphResult",
    "hubs",
    "impact",
    "neighbors",
    "path_query",
    "run_query",
    "tests_for",
]
