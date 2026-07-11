"""Graph tests_for query for ``ralph_graph`` (Phase 2+)."""


from typing import Final

from ralph.mcp.explore.graph import (
    GraphNode,
    GraphResult,
    _current_generation,
    _resolve_target,
)
from ralph.mcp.explore.store import ExploreStore

# Test path conventions. Kept next to tests_for because that
# is the only producer; sharing them via the graph run_query
# namespace would muddy the focused-module contract.
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
    candidate_paths: set[str] = set()
    for edge in store.iter_edges():
        if edge.target_id == resolved_id:
            candidate_paths.add(edge.path)
    # Plus text references to the target's qualified name.
    if resolved_name is not None:
        for edge in store.iter_edges():
            if edge.target_id == resolved_name:
                candidate_paths.add(edge.path)
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
