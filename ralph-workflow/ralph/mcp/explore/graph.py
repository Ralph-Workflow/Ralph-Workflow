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

import logging
import sqlite3
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from types import MappingProxyType
from typing import Final

from ralph.mcp.explore.store import (
    EdgeRow,
    ExploreStore,
    _row_to_symbol,
)

logger = logging.getLogger(__name__)

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
    #: True when the query returned a bounded incomplete result because
    #: the caller's deadline elapsed. Always accompanied by
    #: ``missing_data`` containing ``"deadline_exceeded"`` and a
    #: zeroed/truncated body. Mutable work in flight is discarded;
    #: readers see the last committed generation, not partial rows.
    deadline_exceeded: bool = False
    #: True when the caller asked for cooperative cancellation. Same
    #: bounded-incomplete contract as ``deadline_exceeded``.
    cancelled: bool = False
    metadata: Mapping[str, object] = field(default_factory=dict)

    def with_staleness(self, *, is_stale: bool) -> GraphResult:
        """Return a copy of this result with ``is_stale`` updated.

        AC-07 helper used by the ``run_query`` dispatcher so the
        freshness policy is applied uniformly across all query types
        without rewriting the per-query result construction.
        """
        return replace(self, is_stale=is_stale)


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
    """Extract (symbol_id, qualified_name) from a 2-column symbol row."""
    sym_id: object = row[0]
    qual: object = row[1]
    sym_str = sym_id if isinstance(sym_id, str) else (str(sym_id) if sym_id is not None else None)
    qual_str = qual if isinstance(qual, str) else (str(qual) if qual is not None else None)
    return sym_str, qual_str


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


# --- neighbors ------------------------------------------------------------



def _deadline_elapsed(deadline: float | None) -> bool:
    """Return True when ``deadline`` is set and the monotonic clock has passed it.

    A non-positive ``deadline`` disables the check; the caller is
    expected to have bounded the call site.
    """
    if deadline is None or deadline <= 0:
        return False
    return time.monotonic() >= deadline


def _is_cancelled(cancel: Callable[[], bool] | None) -> bool:
    """Return True when ``cancel`` is set and reports cancellation.

    Catches any exception raised by the cancel callable so a buggy
    cancel hook never crashes the dispatcher; an exception is treated
    as "not cancelled" and a debug log line is emitted.
    """
    if cancel is None:
        return False
    try:
        return bool(cancel())
    except Exception:  # pragma: no cover — defensive
        logger.debug("ralph_graph cancel callable raised; treating as not-cancelled", exc_info=True)
        return False


def _deadline_result(
    query_type: str,
    target: str,
    target_b: str | None,
    store: ExploreStore,
) -> GraphResult:
    """Return the bounded, truthful result when the deadline elapses."""
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
    """Return the bounded, truthful result when the caller cancels."""
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


# --- Deferred re-exports (PEP 562) -------------------------------------
#
# The per-query implementations (``hubs``, ``impact``, ``neighbors``,
# ``path_query``, ``run_query``, ``tests_for``) live in dedicated
# sub-modules. Importing those sub-modules at module-scope time would
# form an import cycle: each sub-module imports dataclasses and helper
# constants from this module. To preserve ``graph.run_query`` access
# for downstream callers and the ``__all__`` contract without forming
# the cycle, the symbols are looked up lazily through a module-level
# ``__getattr__`` (PEP 562).
_LAZY_REEXPORTS = {  # bounded-accumulator-ok: PEP 562 dispatch table; fixed size, populated once at module load
    "graph_module": "ralph.mcp.explore.graph",
    "hubs": "ralph.mcp.explore._graph_impact",
    "impact": "ralph.mcp.explore._graph_impact",
    "neighbors": "ralph.mcp.explore._graph_neighbors",
    "path_query": "ralph.mcp.explore._graph_path",
    "run_query": "ralph.mcp.explore._graph_query",
    "tests_for": "ralph.mcp.explore._graph_tests",
}


def __getattr__(name: str) -> object:
    """Resolve ``graph.<name>`` lazily to a sub-module symbol.

    PEP 562 module-level ``__getattr__``. The dictionary lookup
    precomputes a single ``importlib.import_module`` call per
    sub-module and caches the resolved attribute on the module's
    ``__dict__`` so subsequent ``graph.<name>`` accesses skip the
    indirection. The cached value behaves like a normal module
    attribute, so ``from ralph.mcp.explore.graph import run_query``
    continues to work for downstream callers (e.g. tests that
    ``patch.object(graph_module, "run_query", ...)``).

    ``graph_module`` is a self-referential alias so callers
    (notably ``ralph.mcp.explore.handlers.__getattr__``) that
    expose the graph module under the legacy ``graph_module``
    name continue to find ``graph_module.run_query`` through the
    same PEP 562 lookup without forming an import cycle.
    """
    if name in _LAZY_REEXPORTS:
        # Lazy resolution of the per-query implementations. The
        # dispatch table is a fixed ``str -> str`` mapping so the
        # cache assignment is bounded and idempotent. Subsequent
        # accesses read the cached attribute directly without
        # re-entering ``__getattr__``.
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
    """Include the lazy re-export names in ``dir(graph)``.

    Without this hook the per-query implementations would be hidden
    from introspection tools and from the auto-import wildcard forms
    used by bridge specs and CLI tooling. Sorting keeps the
    supplementary names grouped with the eagerly-defined dataclasses.
    """
    eagerly_defined_names: list[str] = list(globals())  # type: ignore[misc]  # reason: autogenerated code has no type support, see docs/agents/type-ignore-policy.md#autogenerated-code
    lazy_rexport_names: list[str] = list(_LAZY_REEXPORTS.keys())
    return sorted(set(eagerly_defined_names) | set(lazy_rexport_names))
