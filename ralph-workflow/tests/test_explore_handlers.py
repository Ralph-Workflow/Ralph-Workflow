"""Black-box tests for the explore MCP handlers (index_status + reindex)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ralph.mcp.explore.handlers import (
    build_explore_index,
    handle_ralph_index_status,
    handle_ralph_reindex,
)
from ralph.mcp.explore.store import DEFAULT_INDEX_ROOT


class _FakeSession:
    """Minimal session stub."""

    def __init__(self, explore_index=None):
        self.explore_index = explore_index

    def check_capability(self, capability: str):
        return {"status": "approved", "capability": capability}

    def check_edit_area(self, path: str):
        return {"status": "approved", "path": path}


class _Workspace:
    def __init__(self, root: Path) -> None:
        self.root = root


def _seed_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "a.py").write_text("x = 1\n")
    (workspace / "b.py").write_text("y = 2\n")
    return workspace


def _decode(result) -> dict:
    return json.loads(result.content[0].text)


def test_index_status_disabled_when_no_handle(tmp_path: Path) -> None:
    workspace = _seed_workspace(tmp_path)
    session = _FakeSession(explore_index=None)
    result = handle_ralph_index_status(session, _Workspace(workspace), {})
    payload = _decode(result)
    assert payload["enabled"] is False
    assert payload["index_exists"] is False
    assert "generation" in payload
    assert "index_storage_bytes" in payload


def test_index_status_returns_expected_fields(tmp_path: Path) -> None:
    workspace = _seed_workspace(tmp_path)
    handle = build_explore_index(workspace)
    try:
        session = _FakeSession(explore_index=handle)
        result = handle_ralph_index_status(session, _Workspace(workspace), {})
        payload = _decode(result)
        for field in (
            "enabled",
            "index_exists",
            "generation",
            "indexed_at",
            "files_indexed",
            "files_stale",
            "last_job",
            "capabilities",
            "graph_backend",
            "dirty_paths_count",
            "cold_index_required",
            "last_refresh_kind",
            "is_stale",
            "stale_paths_count",
            "index_storage_bytes",
            "gitignore_coverage",
        ):
            assert field in payload, f"missing field: {field}"
        assert payload["enabled"] is True
        assert payload["graph_backend"] == "sqlite"
    finally:
        handle.store.close()


def test_index_status_reports_gitignore_coverage(tmp_path: Path) -> None:
    workspace = _seed_workspace(tmp_path)
    # The repo .gitignore already lists ``.agent/`` (see
    # ``ralph/config/bootstrap.py:_DEFAULT_GITIGNORE_PATTERNS``).
    (workspace / ".gitignore").write_text(".agent/\n")
    handle = build_explore_index(workspace)
    try:
        session = _FakeSession(explore_index=handle)
        result = handle_ralph_index_status(session, _Workspace(workspace), {})
        payload = _decode(result)
        assert payload["gitignore_coverage"]["present"] is True
    finally:
        handle.store.close()


def test_reindex_changed_runs_and_records(tmp_path: Path) -> None:
    workspace = _seed_workspace(tmp_path)
    handle = build_explore_index(workspace)
    try:
        session = _FakeSession(explore_index=handle)
        result = handle_ralph_reindex(
            session,
            _Workspace(workspace),
            {"mode": "changed", "timeout_ms": 5_000},
        )
        payload = _decode(result)
        assert payload["job_status"] == "ok"
        assert payload["generation"] == 1
        assert payload["parse_count"] >= 2
    finally:
        handle.store.close()


def test_reindex_full_rebuilds(tmp_path: Path) -> None:
    workspace = _seed_workspace(tmp_path)
    handle = build_explore_index(workspace)
    try:
        session = _FakeSession(explore_index=handle)
        # First build.
        handle_ralph_reindex(
            session, _Workspace(workspace), {"mode": "changed"}
        )
        # Full rebuild resets the generation to 1.
        result = handle_ralph_reindex(
            session, _Workspace(workspace), {"mode": "full"}
        )
        payload = _decode(result)
        assert payload["generation"] == 1
    finally:
        handle.store.close()


def test_reindex_rejects_invalid_mode(tmp_path: Path) -> None:
    workspace = _seed_workspace(tmp_path)
    handle = build_explore_index(workspace)
    try:
        session = _FakeSession(explore_index=handle)
        from ralph.mcp.tools.coordination import InvalidParamsError

        with pytest.raises(InvalidParamsError):
            handle_ralph_reindex(
                session,
                _Workspace(workspace),
                {"mode": "bogus", "timeout_ms": 5_000},
            )
    finally:
        handle.store.close()


def test_reindex_rejects_zero_timeout(tmp_path: Path) -> None:
    workspace = _seed_workspace(tmp_path)
    handle = build_explore_index(workspace)
    try:
        session = _FakeSession(explore_index=handle)
        from ralph.mcp.tools.coordination import InvalidParamsError

        with pytest.raises(InvalidParamsError):
            handle_ralph_reindex(
                session,
                _Workspace(workspace),
                {"mode": "changed", "timeout_ms": 0},
            )
    finally:
        handle.store.close()


def test_reindex_records_path_scope(tmp_path: Path) -> None:
    workspace = _seed_workspace(tmp_path)
    handle = build_explore_index(workspace)
    try:
        session = _FakeSession(explore_index=handle)
        result = handle_ralph_reindex(
            session,
            _Workspace(workspace),
            {
                "mode": "full",
                "timeout_ms": 5_000,
                "path_scope": ["a.py"],
            },
        )
        payload = _decode(result)
        assert payload["job_status"] == "ok"
        assert "a.py" in payload["changed_files"]
    finally:
        handle.store.close()


def test_build_explore_index_creates_index_dir(tmp_path: Path) -> None:
    workspace = _seed_workspace(tmp_path)
    handle = build_explore_index(workspace)
    try:
        assert handle.index_dir == workspace / DEFAULT_INDEX_ROOT
        assert handle.index_dir.is_dir()
    finally:
        handle.store.close()


def test_reindex_updates_handle_last_refresh_kind(tmp_path: Path) -> None:
    workspace = _seed_workspace(tmp_path)
    handle = build_explore_index(workspace)
    try:
        session = _FakeSession(explore_index=handle)
        handle_ralph_reindex(
            session, _Workspace(workspace), {"mode": "changed"}
        )
        assert handle.last_refresh_kind == "changed"
        handle_ralph_reindex(session, _Workspace(workspace), {"mode": "full"})
        assert handle.last_refresh_kind == "full"
    finally:
        handle.store.close()
