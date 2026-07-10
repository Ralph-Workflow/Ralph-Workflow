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


def _explore_specs_by_name() -> dict[str, object]:
    return {spec.metadata.definition.name: spec for spec in explore_specs()}


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


@pytest.mark.parametrize(
    "query_type",
    ["neighbors", "path", "impact", "tests"],
)
def test_graph_rejects_targetless_required_query_types(
    tmp_path: Path, query_type: str
) -> None:
    """AC-05: ``neighbors``/``path``/``impact``/``tests`` require ``target``.

    The contract documented in
    ``_specs_explore.ralph_graph.description`` states ``target`` is
    required for these four query types and optional only for
    ``hubs``. The handler must enforce that at the parameter boundary
    rather than running a degenerate traversal that returns no
    evidence. ``hubs`` is the single allowed targetless query type
    and is covered separately by
    :func:`test_graph_hubs_accepts_targetless_query`.
    """
    workspace = _seed_workspace(tmp_path)
    store = _build_index(workspace, tmp_path)
    try:
        session = _FakeSession(explore_index=_FakeIndex(store))
        from ralph.mcp.tools.coordination import InvalidParamsError

        with pytest.raises(InvalidParamsError) as exc_info:
            handle_ralph_graph(
                session,
                _Workspace(workspace),
                {"query_type": query_type},
            )
        # The error message must name the missing field and the
        # query type so an agent can repair the call deterministically.
        message = str(exc_info.value)
        assert "target" in message
        assert query_type in message
    finally:
        store.close()


def test_graph_hubs_accepts_targetless_query(tmp_path: Path) -> None:
    """AC-05: ``hubs`` is the single allowed targetless query type."""
    workspace = _seed_workspace(tmp_path)
    store = _build_index(workspace, tmp_path)
    try:
        session = _FakeSession(explore_index=_FakeIndex(store))
        # Must not raise InvalidParamsError for the missing target.
        result = handle_ralph_graph(
            session,
            _Workspace(workspace),
            {"query_type": "hubs"},
        )
        assert result.is_error is False
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


# --- AC-05: bounded timeout_ms and explicit cancellation -----------------


def test_graph_schema_exposes_bounded_timeout_ms_and_cancel() -> None:
    """AC-05: the ralph_graph schema must declare a bounded timeout
    and an explicit cancellation input.
    """
    spec = _explore_specs_by_name()[RALPH_GRAPH_TOOL]
    properties = spec.metadata.definition.input_schema["properties"]
    assert "timeout_ms" in properties
    timeout = properties["timeout_ms"]
    assert timeout["type"] == "integer"
    assert timeout.get("minimum") == 1
    assert timeout.get("maximum") == 30_000
    assert int(timeout["default"]) == 5_000
    assert "cancel" in properties
    assert properties["cancel"]["type"] == "boolean"


def test_graph_rejects_oversized_timeout_ms(tmp_path: Path) -> None:
    """AC-05: callers cannot extend the budget arbitrarily. The
    handler rejects ``timeout_ms`` above the documented cap.
    """
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
                    "timeout_ms": 9_999_999_999,
                },
            )
    finally:
        store.close()


def test_graph_rejects_zero_timeout_ms(tmp_path: Path) -> None:
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
                    "timeout_ms": 0,
                },
            )
    finally:
        store.close()


def test_graph_rejects_negative_timeout_ms(tmp_path: Path) -> None:
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
                    "timeout_ms": -1,
                },
            )
    finally:
        store.close()


def test_graph_deadline_exceeded_returns_bounded_incomplete_result(
    tmp_path: Path,
) -> None:
    """AC-05: when the deadline elapses before the query starts,
    the dispatcher returns a bounded, truthful incomplete result
    with ``deadline_exceeded=true`` and the missing-data marker.
    No mutable work is exposed to readers.
    """
    import time as _time

    from ralph.mcp.explore import graph as graph_module
    from ralph.mcp.explore.store import ExploreStore

    _seed_workspace(tmp_path)
    store = ExploreStore(tmp_path / ".agent" / "ralph-explore")
    try:
        # Pick a deadline already in the past.
        past = _time.monotonic() - 0.1
        result = graph_module.run_query(
            store,
            query_type="neighbors",
            target="module.hello",
            deadline=past,
        )
        assert result.deadline_exceeded is True
        assert result.cancelled is False
        assert "deadline_exceeded" in result.missing_data
        assert result.nodes == ()
        assert result.edges == ()
        assert result.paths == ()
        assert result.impacted_files == ()
        assert result.suggested_tests == ()
        assert result.truncated is True
        # index_generation reports the last committed value (0 here).
        assert result.index_generation == 0
    finally:
        store.close()


def test_graph_cancel_request_returns_bounded_incomplete_result(
    tmp_path: Path,
) -> None:
    """AC-05: ``cancel=true`` returns a bounded, truthful
    incomplete result with ``cancelled=true`` and the
    ``cancelled`` marker in ``missing_data``.
    """
    from ralph.mcp.explore import graph as graph_module
    from ralph.mcp.explore.store import ExploreStore

    _seed_workspace(tmp_path)
    store = ExploreStore(tmp_path / ".agent" / "ralph-explore")
    try:
        result = graph_module.run_query(
            store,
            query_type="neighbors",
            target="module.hello",
            cancel=lambda: True,
        )
        assert result.cancelled is True
        assert result.deadline_exceeded is False
        assert "cancelled" in result.missing_data
        assert result.nodes == ()
        assert result.edges == ()
        assert result.truncated is True
    finally:
        store.close()


def test_graph_cancel_via_session_attribute_honored(tmp_path: Path) -> None:
    """AC-05: the handler reads ``cancel=true`` and sets the
    session cancel flag; the dispatcher honors it on the next
    call and returns a bounded incomplete result.
    """
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
                "cancel": True,
            },
        )
        payload = _decode(result)
        assert payload["cancelled"] is True
        assert payload["deadline_exceeded"] is False
        assert "cancelled" in payload["missing_data"]
        assert payload["nodes"] == []
        assert payload["edges"] == []
    finally:
        store.close()


def test_graph_handler_returns_cancelled_and_deadline_fields(
    tmp_path: Path,
) -> None:
    """The response payload always carries ``cancelled`` and
    ``deadline_exceeded`` so callers can branch on them without
    a second round-trip.
    """
    workspace = _seed_workspace(tmp_path)
    store = _build_index(workspace, tmp_path)
    try:
        session = _FakeSession(explore_index=_FakeIndex(store))
        result = handle_ralph_graph(
            session,
            _Workspace(workspace),
            {"query_type": "neighbors", "target": "module.hello"},
        )
        payload = _decode(result)
        assert "cancelled" in payload
        assert "deadline_exceeded" in payload
        # Default path: both are False.
        assert payload["cancelled"] is False
        assert payload["deadline_exceeded"] is False
    finally:
        store.close()


def test_graph_rejects_malformed_timeout_string(tmp_path: Path) -> None:
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
                    "timeout_ms": "lots",
                },
            )
    finally:
        store.close()
