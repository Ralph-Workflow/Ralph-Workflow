
from __future__ import annotations

import logging
import sqlite3
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from types import MappingProxyType
from typing import Final, cast

from ralph.mcp.explore.store import (
    EdgeRow,
    ExploreStore,
    _row_to_symbol,
)

logger = logging.getLogger(__name__)

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

    id: str
    kind: str
    label: str
    path: str
    confidence: float
    provenance: str
    evidence_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class GraphEdge:

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
    deadline_exceeded: bool = False
    cancelled: bool = False
    metadata: Mapping[str, object] = field(default_factory=dict)

    def with_staleness(self, *, is_stale: bool) -> GraphResult:
        return replace(self, is_stale=is_stale)


def _relation_priority(relation: str) -> int:
    try:
        return _RELATION_PRIORITY.index(relation)
    except ValueError:
        return len(_RELATION_PRIORITY)


def _resolve_target(store: ExploreStore, target: str) -> tuple[str | None, str | None]:
    if not target:
        return None, None
    cur = store._conn.execute(
        "SELECT symbol_id, qualified_name FROM symbols "
        "WHERE qualified_name = ? OR symbol_id = ? LIMIT 1",
        (target, target),
    )
    row: sqlite3.Row | None = cur.fetchone()
    if row is not None:
        return _extract_symbol_id_name(row)
    if target.endswith(".py") or target.endswith(".md"):
        cur = store._conn.execute(
            "SELECT symbol_id, qualified_name FROM symbols "
            "WHERE path = ? ORDER BY qualified_name LIMIT 1",
            (target,),
        )
        row2: sqlite3.Row | None = cur.fetchone()
        if row2 is not None:
            return _extract_symbol_id_name(row2)
        return f"file:{target}", target
    return None, None


def _extract_symbol_id_name(row: sqlite3.Row) -> tuple[str | None, str | None]:
    sym_id: object = row[0]
    qual: object = row[1]
    sym_str = sym_id if isinstance(sym_id, str) else (str(sym_id) if sym_id is not None else None)
    qual_str = qual if isinstance(qual, str) else (str(qual) if qual is not None else None)
    return sym_str, qual_str


def _row_to_edge(row: EdgeRow) -> GraphEdge:
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
    typed = _row_to_symbol(row)
    return GraphNode(
        id=typed.symbol_id,
        kind=typed.kind,
        label=typed.qualified_name,
        path=typed.path,
        confidence=typed.confidence,
        provenance=typed.extracted_from,
        evidence_ids=(typed.span_id, typed.symbol_id),
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
    for edge in store.iter_edges():
        if edge.source_id != source_id:
            continue
        if relations and edge.relation not in relations:
            continue
        yield edge


def _iter_incoming(
    store: ExploreStore,
    *,
    target_id: str,
    relations: Sequence[str],
) -> Iterable[EdgeRow]:
    for edge in store.iter_edges():
        if edge.target_id != target_id:
            continue
        if relations and edge.relation not in relations:
            continue
        yield edge




def _deadline_elapsed(deadline: float | None) -> bool:
    if deadline is None or deadline <= 0:
        return False
    return time.monotonic() >= deadline


def _is_cancelled(cancel: Callable[[], bool] | None) -> bool:
    if cancel is None:
        return False
    try:
        return bool(cancel())
    except Exception:
        logger.debug("ralph_graph cancel callable raised; treating as not-cancelled", exc_info=True)
        return False


def _deadline_result(
    query_type: str,
    target: str,
    target_b: str | None,
    store: ExploreStore,
) -> GraphResult:
    return GraphResult(
        query_type=query_type,
        nodes=(),
        edges=(),
        paths=(),
        impacted_files=(),
        suggested_tests=(),
        confidence=0.0,
        provenance="deadline",
        evidence_ids=(),
        missing_data=("deadline_exceeded",),
        index_generation=_current_generation(store),
        is_stale=False,
        truncated=True,
        deadline_exceeded=True,
        cancelled=False,
        metadata={
            "target": target,
            "target_b": target_b,
            "bounded_incomplete": True,
        },
    )


def _cancelled_result(
    query_type: str,
    target: str,
    target_b: str | None,
    store: ExploreStore,
) -> GraphResult:
    return GraphResult(
        query_type=query_type,
        nodes=(),
        edges=(),
        paths=(),
        impacted_files=(),
        suggested_tests=(),
        confidence=0.0,
        provenance="cancelled",
        evidence_ids=(),
        missing_data=("cancelled",),
        index_generation=_current_generation(store),
        is_stale=False,
        truncated=True,
        deadline_exceeded=False,
        cancelled=True,
        metadata={
            "target": target,
            "target_b": target_b,
            "bounded_incomplete": True,
        },
    )


__all__ = [
    "GraphEdge",
    "GraphNode",
    "GraphResult",
]


_LAZY_REEXPORTS = {
    "graph_module": "ralph.mcp.explore.graph",
    "hubs": "ralph.mcp.explore._graph_impact",
    "impact": "ralph.mcp.explore._graph_impact",
    "neighbors": "ralph.mcp.explore._graph_neighbors",
    "path_query": "ralph.mcp.explore._graph_path",
    "run_query": "ralph.mcp.explore._graph_query",
    "tests_for": "ralph.mcp.explore._graph_tests",
}


def __getattr__(name: str) -> object:
    if name in _LAZY_REEXPORTS:
        import importlib
        import sys

        sub_module = importlib.import_module(_LAZY_REEXPORTS[name])
        if name == "graph_module":
            value: object = sub_module
        else:
            value = getattr(sub_module, name)
        setattr(sys.modules[__name__], name, value)
        return value
    raise AttributeError(f"module 'ralph.mcp.explore.graph' has no attribute {name!r}")


def __dir__() -> list[str]:
    module_globals: dict[str, str] = cast("dict[str, str]", globals())
    eagerly_defined_names: list[str] = list(module_globals)
    lazy_rexport_names: list[str] = list(_LAZY_REEXPORTS.keys())
    return sorted(set(eagerly_defined_names) | set(lazy_rexport_names))
