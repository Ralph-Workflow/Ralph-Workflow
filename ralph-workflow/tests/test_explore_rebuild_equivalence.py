"""AC-8 equivalence test: wiping the explore store on disk and
rebuilding it produces an equivalent search-result set for a
deterministic query.

The companion ``tests/unit/test_explore_pipeline_invariants.py``
checks that the *parse count* survives a wipe + rebuild. This test
goes deeper: it verifies that the actual **search-result sets**
(ranked ``handle_search_files`` paths and focused
``handle_grep_files`` span hits) are equal before and after wiping
the explore store on disk and rebuilding from scratch. Path-set
equality (not internal row IDs) is asserted so the proof rests on
what an agent observes, not on volatile storage details.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.mcp.explore.dirty_paths import build_sqlite_index_handle
from ralph.mcp.explore.pipeline import ReindexOptions, reindex
from ralph.mcp.explore.store import ExploreStore
from ralph.mcp.tools.workspace._grep_handlers import handle_grep_files
from ralph.mcp.tools.workspace._read_handlers import handle_search_files

if TYPE_CHECKING:
    from ralph.mcp.tools.tool_result import ToolResult

# Shared literal token embedded in every corpus file so the literal
# grep query has a deterministic, FTS-eligible hit set.
_SENTINEL = "rebuild_equivalence_sentinel"


class _FakeSession:
    """Minimal coordination session for handler tests."""

    def __init__(self, explore_index: object | None = None) -> None:
        self.explore_index = explore_index

    def check_capability(self, capability: str) -> dict[str, str]:
        return {"status": "approved", "capability": capability}

    def check_edit_area(self, path: str) -> dict[str, str]:
        return {"status": "approved", "path": path}


class _Workspace:
    """Minimal workspace adapter for handler tests."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def read(self, path: str) -> str:
        return (self.root / path).read_text()

    def iter_files(self, base: str) -> object:
        base_path = self.root / base if base else self.root
        return (
            str(path.relative_to(self.root))
            for path in base_path.rglob("*")
            if path.is_file()
        )


def _decode(result: ToolResult) -> dict[str, object]:
    return json.loads(result.content[0].text)


def _seed_corpus(tmp_path: Path) -> Path:
    """Seed a workspace with 12 Python files of mixed case and dotted names."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    files: dict[str, str] = {
        "a.py": f"{_SENTINEL} = 1\ndef fn_a():\n    return 1\n",
        "B.py": f"{_SENTINEL} = 2\ndef fn_B():\n    return 2\n",
        "CamelCase.py": f"{_SENTINEL} = 3\ndef fnCamel():\n    return 3\n",
        "snake_case.py": f"{_SENTINEL} = 4\ndef fn_snake():\n    return 4\n",
        "dotted.name.py": f"{_SENTINEL} = 5\ndef fn_dotted():\n    return 5\n",
        "UPPER.py": f"{_SENTINEL} = 6\ndef fn_UPPER():\n    return 6\n",
        "pkg/__init__.py": f"{_SENTINEL} = 7\n",
        "pkg/module.py": f"{_SENTINEL} = 8\ndef fn_pkg_module():\n    return 8\n",
        "pkg/Nested.py": f"{_SENTINEL} = 9\ndef fn_nested():\n    return 9\n",
        "pkg/sub/__init__.py": f"{_SENTINEL} = 10\n",
        "pkg/sub/leaf.py": f"{_SENTINEL} = 11\ndef fn_leaf():\n    return 11\n",
        "z.py": f"{_SENTINEL} = 12\ndef fn_z():\n    return 12\n",
    }
    for rel_path, content in files.items():
        target = workspace / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    return workspace


def _capture_search_paths(session: _FakeSession, ws: _Workspace) -> set[str]:
    """Run a ranked handle_search_files and return the matched path set."""
    result = handle_search_files(
        session,
        ws,
        {"pattern": "**/*.py", "path": ".", "ranked": True},
    )
    payload = _decode(result)
    matches = payload["matches"]
    assert isinstance(matches, list)
    return {str(p) for p in matches}


def _capture_grep_spans(session: _FakeSession, ws: _Workspace) -> set[tuple[str, int]]:
    """Run a focused handle_grep_files and return the (path, line) span set."""
    result = handle_grep_files(
        session,
        ws,
        {"pattern": _SENTINEL, "path": ".", "regex": False, "case_sensitive": True},
    )
    payload = _decode(result)
    matches = payload["matches"]
    assert isinstance(matches, list)
    return {(str(m["path"]), int(m["line"])) for m in matches}


def test_wipe_and_rebuild_produces_equivalent_search_results(
    tmp_path: Path,
) -> None:
    """AC-8: wiping the explore store on disk and rebuilding produces an
    equivalent search-result set for a deterministic query.

    Asserts path-set equality (not internal row IDs) so a wipe + rebuild
    is observationally equivalent to the prior live state.
    """
    workspace = _seed_corpus(tmp_path)
    store_dir = tmp_path / ".agent" / "ralph-explore"

    # --- Initial build ---
    store = ExploreStore(store_dir)
    try:
        first = reindex(store, workspace, options=ReindexOptions(timeout_ms=5000))
        assert first.status == "ok"
        assert first.parse_count >= 8

        session = _FakeSession(explore_index=build_sqlite_index_handle(store))
        ws = _Workspace(workspace)

        search_paths_before = _capture_search_paths(session, ws)
        grep_spans_before = _capture_grep_spans(session, ws)
    finally:
        store.close()

    # Sanity: both probes returned non-trivial sets.
    assert len(search_paths_before) >= 8
    assert len(grep_spans_before) >= 8

    # --- Wipe the derived intelligence directory and rebuild from scratch ---
    shutil.rmtree(store_dir)
    store = ExploreStore(store_dir)
    try:
        rebuilt = reindex(
            store,
            workspace,
            options=ReindexOptions(mode="full", timeout_ms=5000),
        )
        assert rebuilt.status == "ok"
        assert rebuilt.parse_count >= 8

        session = _FakeSession(explore_index=build_sqlite_index_handle(store))
        ws = _Workspace(workspace)

        search_paths_after = _capture_search_paths(session, ws)
        grep_spans_after = _capture_grep_spans(session, ws)
    finally:
        store.close()

    # AC-8: path-set equality -- a wipe + rebuild is observationally
    # equivalent to the prior live state. We compare path-sets and
    # (path, line) span-sets, NOT internal row IDs, so the proof
    # rests on what an agent observes.
    assert search_paths_before == search_paths_after
    assert grep_spans_before == grep_spans_after
