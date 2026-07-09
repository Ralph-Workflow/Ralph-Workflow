"""Focused tests for handle_directory_tree changed_only filtering (AC-09).

Prior analysis how_to_fix item 8 requires directory_tree to parse
and apply ``changed_only`` using the same dirty-path source as
``list_directory`` (peek_dirty_paths from the explore store).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest

from ralph.mcp.explore.dirty_paths import build_sqlite_index_handle
from ralph.mcp.explore.store import ExploreStore
from ralph.mcp.tools.coordination import CapabilityDeniedError, ToolContent
from ralph.mcp.tools.workspace import (
    WORKSPACE_READ_CAPABILITY,
    handle_directory_tree,
)
from tests.mock_session import MockSession


def _flat_paths(node: dict[str, object]) -> list[str]:
    """Return ``path`` fields (depth-first) for every node in the tree."""

    paths: list[str] = []
    node_type_obj = node.get("type")
    if not isinstance(node_type_obj, str):
        return paths
    node_path_obj = node.get("path")
    if isinstance(node_path_obj, str) and node_path_obj:
        paths.append(node_path_obj)
    children_obj = node.get("children")
    if isinstance(children_obj, list):
        for child in children_obj:
            if isinstance(child, dict):
                paths.extend(_flat_paths(child))
    return paths


class _FakeSession:
    """Minimal session with capability check + explore_index slot."""

    def __init__(self, explore_index: Any | None, *, capability: bool) -> None:
        self.explore_index = explore_index
        self._has_capability = capability
        self._caps = {WORKSPACE_READ_CAPABILITY} if capability else set()

    def check_capability(self, capability: str) -> object:
        return capability in self._caps


class _TreeWorkspace:
    """Workspace stub whose contents are returned verbatim."""

    def __init__(self, children_by_dir: dict[str, list[str]]) -> None:
        self._children = children_by_dir

    def is_dir(self, path: str) -> bool:
        return path in self._children

    def is_file(self, path: str) -> bool:
        for entries in self._children.values():
            for entry in entries:
                if path == entry or path.endswith("/" + entry):
                    return True
        return False

    def exists(self, path: str) -> bool:
        return self.is_dir(path) or self.is_file(path)

    def list_dir(self, path: str) -> list[str]:
        return list(self._children.get(path, ()))


def _session_with_store(store: ExploreStore) -> _FakeSession:
    return _FakeSession(build_sqlite_index_handle(store), capability=True)


def _open_store(tmp_path: Path) -> ExploreStore:
    store = ExploreStore(tmp_path / "index.sqlite")
    return store


def test_changed_only_returns_only_dirty_subtree(tmp_path: Path) -> None:
    children: dict[str, list[str]] = {
        "": ["docs", "src", "README.md"],
        "docs": ["index.md", "changelog.md"],
        "src": ["app.py", "lib.py"],
    }
    store = _open_store(tmp_path)
    store.mark_dirty("src/app.py", reason="mutated", source_tool="write_file")

    result = handle_directory_tree(
        _session_with_store(store),
        _TreeWorkspace(children),
        {"path": "", "changed_only": True},
    )
    assert result.is_error is False
    payload = json.loads(cast("ToolContent", result.content[0]).text)
    paths = _flat_paths(payload["tree"])

    # The changed subtree is present (file + every ancestor kept).
    assert "src/app.py" in paths
    assert "src" in paths
    # Nothing unrelated leaks through.
    assert all(not p.startswith("docs") for p in paths if p)
    assert "docs/index.md" not in paths
    assert "docs/changelog.md" not in paths
    assert "README.md" not in paths
    assert "src/lib.py" not in paths
    assert payload["changed_only"] is True
    assert payload["index_used"] is True


def test_changed_only_empty_dirty_set_omits_all_subtrees(tmp_path: Path) -> None:
    children: dict[str, list[str]] = {
        "": ["docs", "src", "README.md"],
        "docs": ["index.md"],
        "src": ["app.py"],
    }
    store = _open_store(tmp_path)

    result = handle_directory_tree(
        _session_with_store(store),
        _TreeWorkspace(children),
        {"path": "", "changed_only": True},
    )
    assert result.is_error is False
    payload = json.loads(cast("ToolContent", result.content[0]).text)
    paths = _flat_paths(payload["tree"])
    # Empty dirty set should drop every subtree.
    assert paths == []


def test_changed_only_use_index_always_without_handle_fails_closed() -> None:
    ws = _TreeWorkspace({"": ["only.txt"]})
    result = handle_directory_tree(
        _FakeSession(None, capability=True),
        ws,
        {"path": "", "changed_only": True, "use_index": "always"},
    )
    assert result.is_error is True
    payload = json.loads(cast("ToolContent", result.content[0]).text)
    assert payload["reason"] == "no_explore_index_handle"


def test_changed_only_use_index_never_preserves_raw_tree(tmp_path: Path) -> None:
    ws = _TreeWorkspace({"": ["a", "b"]})
    result = handle_directory_tree(
        _FakeSession(None, capability=True),
        ws,
        {"path": "", "changed_only": True, "use_index": "never"},
    )
    assert result.is_error is False
    payload = json.loads(cast("ToolContent", result.content[0]).text)
    names = sorted(
        child.get("name")
        for child in cast("list[dict[str, object]]", payload.get("children", []))
        if isinstance(child, dict)
    )
    # Raw tree unchanged - both a and b appear regardless of dirty state.
    assert names == ["a", "b"]


def test_changed_only_reports_is_stale_metadata(tmp_path: Path) -> None:
    children: dict[str, list[str]] = {"": ["src"], "src": ["app.py"]}
    store = _open_store(tmp_path)
    store.mark_dirty("src/app.py", reason="mutated", source_tool="write_file")

    result = handle_directory_tree(
        _session_with_store(store),
        _TreeWorkspace(children),
        {"path": "", "changed_only": True},
    )
    assert result.is_error is False
    payload = json.loads(cast("ToolContent", result.content[0]).text)
    assert payload["changed_only"] is True
    assert payload["is_stale"] is True


def test_changed_only_capability_missing(tmp_path: Path) -> None:
    ws = _TreeWorkspace({"": ["x"]})

    with pytest.raises(CapabilityDeniedError):
        handle_directory_tree(
            _FakeSession(None, capability=False),
            ws,
            {"path": "", "changed_only": True},
        )


def test_changed_only_non_dirty_view_preserves_existing_tests(tmp_path: Path) -> None:
    # Regression: live ``view == "raw" + use_index == "never"`` path must
    # still ignore ``changed_only`` entirely (raw tree shape preserved).
    ws = _TreeWorkspace({"": ["a"]})
    result = handle_directory_tree(
        MockSession(WORKSPACE_READ_CAPABILITY),
        ws,
        {"path": "", "use_index": "never"},
    )
    assert result.is_error is False
