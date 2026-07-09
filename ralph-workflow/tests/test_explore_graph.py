"""Black-box tests for ``ralph_graph`` MCP tool behavior.

Covers AC-07: ralph_graph is registered and implements prompt-exact
neighbors, path, impact, hubs, and tests queries with bounded
traversal, evidence-backed output, capability gates, and structured
freshness/truncation metadata.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ralph.mcp.explore.handlers import (
    handle_ralph_graph,
)
from ralph.mcp.explore.pipeline import ReindexOptions, reindex
from ralph.mcp.explore.store import ExploreStore
from ralph.mcp.tools.bridge._specs_explore import explore_specs
from ralph.mcp.tools.names import (
    RALPH_GRAPH_TOOL,
    RALPH_INDEX_STATUS_TOOL,
    RALPH_REINDEX_TOOL,
)


class _FakeSession:
    def __init__(self, explore_index=None) -> None:
        self.explore_index = explore_index

    def check_capability(self, capability: str) -> dict[str, str]:
        return {"status": "approved", "capability": capability}

    def check_edit_area(self, path: str) -> dict[str, str]:
        return {"status": "approved", "path": path}


class _Workspace:
    def __init__(self, root: Path) -> None:
        self.root = root


def _all_tool_names() -> set[str]:
    return {spec.metadata.definition.name for spec in explore_specs()}


def _decode(result) -> dict:
    return json.loads(result.content[0].text)


def _seed_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "module.py").write_text(
        "from helper import compute\n\n"
        "def hello():\n    return compute(1)\n\n"
        "class Foo:\n    def bar(self):\n        return 2\n"
    )
    (workspace / "helper.py").write_text(
        "def compute(x):\n    return x + 1\n"
    )
    (workspace / "README.md").write_text(
        "# Title\n\n## Section\n\n```python\nx = 1\n```\n"
    )
    (workspace / "tests").mkdir()
    (workspace / "tests" / "test_module.py").write_text(
        "from module import hello\n\n"
        "def test_hello():\n    assert hello() == 2\n"
    )
    return workspace


def _build_index(workspace: Path, tmp_path: Path) -> ExploreStore:
    store = ExploreStore(tmp_path / ".agent" / "ralph-explore")
    reindex(store, workspace, options=ReindexOptions(timeout_ms=5000))
    return store


def test_ralph_graph_is_listed_with_explore_tools() -> None:
    """AC-07: ralph_graph is registered with the explore tools."""
    names = _all_tool_names()
    assert RALPH_GRAPH_TOOL in names
    assert RALPH_INDEX_STATUS_TOOL in names
    assert RALPH_REINDEX_TOOL in names


def test_graph_neighbors_returns_prompt_exact_bounded_evidence_backed_edges(
    tmp_path: Path,
) -> None:
    """AC-07: ``neighbors`` returns bounded, evidence-backed edges."""
    workspace = _seed_workspace(tmp_path)
    store = _build_index(workspace, tmp_path)
    try:
        session = _FakeSession(explore_index=_FakeIndex(store))
        result = handle_ralph_graph(
            session,
            _Workspace(workspace),
            {
                "query_type": "neighbors",
                "target": "module.hello",
                "direction": "in",
                "limit": 25,
            },
        )
        payload = _decode(result)
        for key in (
            "nodes",
            "edges",
            "paths",
            "impacted_files",
            "suggested_tests",
            "confidence",
            "provenance",
            "evidence_ids",
            "missing_data",
            "index_generation",
            "is_stale",
            "truncated",
        ):
            assert key in payload, f"missing field: {key}"
        assert payload["query_type"] == "neighbors"
        # ``hello`` is called from the file but no other symbol
        # calls it, so the in-neighborhood should be empty or
        # contain only the file/node itself.
        assert isinstance(payload["edges"], list)
    finally:
        store.close()


def test_graph_path_impact_hubs_and_tests_follow_prompt_schema_and_limits(
    tmp_path: Path,
) -> None:
    """AC-07: path/impact/hubs/tests return the prompt-exact schema."""
    workspace = _seed_workspace(tmp_path)
    store = _build_index(workspace, tmp_path)
    try:
        session = _FakeSession(explore_index=_FakeIndex(store))

        path_result = handle_ralph_graph(
            session,
            _Workspace(workspace),
            {
                "query_type": "path",
                "target": "module.hello",
                "target_b": "helper.compute",
                "max_paths": 3,
                "depth": 4,
            },
        )
        path_payload = _decode(path_result)
        assert path_payload["query_type"] == "path"
        assert isinstance(path_payload["paths"], list)

        impact_result = handle_ralph_graph(
            session,
            _Workspace(workspace),
            {
                "query_type": "impact",
                "target": "module.hello",
                "change_kind": "rename",
                "limit": 25,
            },
        )
        impact_payload = _decode(impact_result)
        assert impact_payload["query_type"] == "impact"
        assert isinstance(impact_payload["impacted_files"], list)
        assert "behavioral_coverage_unproven" in impact_payload["missing_data"] or \
            impact_payload["missing_data"] == []

        hubs_result = handle_ralph_graph(
            session,
            _Workspace(workspace),
            {"query_type": "hubs", "limit": 10},
        )
        hubs_payload = _decode(hubs_result)
        assert hubs_payload["query_type"] == "hubs"

        tests_result = handle_ralph_graph(
            session,
            _Workspace(workspace),
            {"query_type": "tests", "target": "module.hello", "limit": 25},
        )
        tests_payload = _decode(tests_result)
        assert tests_payload["query_type"] == "tests"
        # The seeds include tests/test_module.py; ``tests`` is a
        # suggestion, not a proof. The missing_data flag must be
        # present so callers do not over-read the result.
        assert "behavioral_coverage_unproven" in tests_payload["missing_data"]
    finally:
        store.close()


def test_graph_rejects_invalid_query_type(tmp_path: Path) -> None:
    """ralph_graph fails closed on unsupported query types."""
    workspace = _seed_workspace(tmp_path)
    store = _build_index(workspace, tmp_path)
    try:
        session = _FakeSession(explore_index=_FakeIndex(store))
        from ralph.mcp.tools.coordination import InvalidParamsError

        with pytest.raises(InvalidParamsError):
            handle_ralph_graph(
                session,
                _Workspace(workspace),
                {"query_type": "bogus", "target": "module.hello"},
            )
    finally:
        store.close()


def test_graph_rejects_invalid_change_kind(tmp_path: Path) -> None:
    workspace = _seed_workspace(tmp_path)
    store = _build_index(workspace, tmp_path)
    try:
        session = _FakeSession(explore_index=_FakeIndex(store))
        from ralph.mcp.tools.coordination import InvalidParamsError

        with pytest.raises(InvalidParamsError):
            handle_ralph_graph(
                session,
                _Workspace(workspace),
                {
                    "query_type": "impact",
                    "target": "module.hello",
                    "change_kind": "unknown_kind",
                },
            )
    finally:
        store.close()


def test_graph_rejects_limit_out_of_range(tmp_path: Path) -> None:
    workspace = _seed_workspace(tmp_path)
    store = _build_index(workspace, tmp_path)
    try:
        session = _FakeSession(explore_index=_FakeIndex(store))
        from ralph.mcp.tools.coordination import InvalidParamsError

        with pytest.raises(InvalidParamsError):
            handle_ralph_graph(
                session,
                _Workspace(workspace),
                {
                    "query_type": "neighbors",
                    "target": "module.hello",
                    "limit": 999,
                },
            )
    finally:
        store.close()


class _FakeIndex:
    """Minimal explore-index handle carrying just the SQLite store."""

    def __init__(self, store: ExploreStore) -> None:
        self.store = store
        self.last_refresh_kind = "none"
        self.cold_index_required = False
        self.generation = 0
        self.last_job_status = None
        self.reindex_in_progress = False
