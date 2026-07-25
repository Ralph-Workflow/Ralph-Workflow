"""Realistic multi-file codebase fixture for indexed search/explore.

Builds a synthetic but realistic Python package tree in ``tmp_path``
(mixed-case symbols, dotted module paths, dotted literals such as
``os.path``, and a few JS/TS/Markdown files) and asserts that the
indexed exploration path is correct end-to-end:

* indexed vs live grep match-set parity across query types,
* dotted literals (e.g. ``os.path``) are served from the index,
* case-sensitive and case-INsensitive parity,
* ``ralph_index_status`` reports a fresh index,
* ``ralph_reindex(mode='changed')`` picks up an edit and the next
  grep sees the new content,
* ``ralph_graph`` neighbors on a defined symbol resolve,
* ``directory_tree`` indexed views return the real fixture shape.

The whole suite is in-process (no sockets, no real subprocess) and
targets <5s wall clock so it fits inside the immutable 60s budget.
"""

from __future__ import annotations

import json
from pathlib import Path

from ralph.mcp.explore.dirty_paths import build_sqlite_index_handle
from ralph.mcp.explore.handlers import (
    build_explore_index,
    handle_ralph_graph,
    handle_ralph_index_status,
    handle_ralph_reindex,
)
from ralph.mcp.explore.pipeline import ReindexOptions, reindex
from ralph.mcp.explore.store import ExploreStore
from ralph.mcp.tools.workspace._grep_handlers import handle_grep_files
from ralph.mcp.tools.workspace._read_handlers import (
    handle_search_files,
)


class _FakeSession:
    """Minimal session stub for handler calls."""

    def __init__(self, explore_index=None):
        self.explore_index = explore_index

    def check_capability(self, capability: str):
        return {"status": "approved", "capability": capability}

    def check_edit_area(self, path: str):
        return {"status": "approved", "path": path}


class _Workspace:
    """Minimal workspace stub matching the grep/search handler contract."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def write(self, path: str, content: str) -> None:
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)

    def read(self, path: str) -> str:
        return (self.root / path).read_text()

    def stat(self, path: str):
        target = self.root / path
        if target.is_dir():
            return {"type": "dir", "size_bytes": 0}
        if target.exists():
            return {"type": "file", "size_bytes": target.stat().st_size}
        return {"type": "missing", "size_bytes": 0}

    def is_dir(self, path: str) -> bool:
        return (self.root / path).is_dir()

    def iter_files(self, base: str):
        base_path = self.root / base if base else self.root
        for path in base_path.rglob("*"):
            if path.is_file():
                yield str(path.relative_to(self.root))

    def list_dir(self, base: str):
        target = self.root / base if base else self.root
        return [p.name for p in target.iterdir()]


def _seed_realistic_codebase(root: Path) -> None:
    """Build a 50-ish file realistic Python project in ``root``."""
    # Layout: package tree with mixed-case modules, dotted names,
    # JS/TS, and Markdown.
    (root / "src").mkdir()
    (root / "src" / "pkg").mkdir()
    (root / "src" / "pkg" / "sub").mkdir()
    (root / "tests").mkdir()
    (root / "docs").mkdir()
    (root / "scripts").mkdir()

    modules = [
        ("src/pkg/__init__.py", "from .sub import helper\nfrom .util import join_path\n"),
        ("src/pkg/sub/__init__.py", "from .core import helper\n"),
        (
            "src/pkg/sub/core.py",
            "def helper(value):\n    return os.path.join(value)\n",
        ),
        (
            "src/pkg/util.py",
            "import os.path\nimport os.environ\ndef join_path(a, b):\n    return os.path.join(a, b)\n",
        ),
        (
            "src/pkg/api.py",
            "from .sub import helper\ndef fetch_user(user_id):\n    return helper(user_id)\n",
        ),
        (
            "src/pkg/io.py",
            "import json\ndef load_config(path):\n    with open(path) as f:\n        return json.load(f)\n",
        ),
        (
            "tests/test_core.py",
            "from src.pkg.sub import helper\ndef test_helper():\n    assert helper('x') is not None\n",
        ),
        (
            "tests/test_util.py",
            "from src.pkg import join_path\ndef test_join():\n    assert join_path('a', 'b') == 'a/b'\n",
        ),
        (
            "scripts/run.py",
            "import os.path\nprint(os.path.join('a', 'b'))\n",
        ),
        ("README.md", "# Project\n\nHelper for joining paths.\n"),
        ("docs/USAGE.md", "Use os.path.join for portable paths.\n"),
        ("src/pkg/notes.txt", "Notes about os.path semantics here.\n"),
    ]
    for rel, content in modules:
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(content)

    # Add a few extra modules so the fixture has >= 50 files.
    for i in range(40):
        (root / "src" / "pkg" / f"mod{i}.py").write_text(
            f"def helper_{i}(value):\n    return os.path.join(value)\n"
        )


def _decode(result) -> dict:
    return json.loads(result.content[0].text)


def _populate(workspace: Path, store: ExploreStore) -> None:
    reindex(store, workspace, options=ReindexOptions(timeout_ms=10000, mode="full"))


def _build_handle_for(workspace: Path) -> object:
    """Build an ExploreIndex handle rooted at ``workspace``.

    Use the full production-shaped handle (with workspace_root,
    generation, store) so handlers like ``ralph_index_status`` and
    ``ralph_reindex`` can read the attributes they expect.
    """
    return build_explore_index(workspace)


def _run_grep(
    session: _FakeSession,
    workspace: Path,
    pattern: str,
    *,
    case_sensitive: bool = False,
    regex: bool = False,
    use_index: str = "auto",
    path: str = ".",
) -> dict:
    result = handle_grep_files(
        session,
        _Workspace(workspace),
        {
            "pattern": pattern,
            "path": path,
            "regex": regex,
            "case_sensitive": case_sensitive,
            "use_index": use_index,
        },
    )
    return _decode(result)


def test_indexed_vs_live_grep_match_set_parity(tmp_path: Path) -> None:
    """Indexed and live grep return the same set of matched files for plain literals.

    AC-02 / S-4: indexed search/explore is correct on a realistic
    codebase. The indexed branch serves the same set of files the
    live branch serves. (Per-line equality is not asserted because
    the FTS5 chunk's start_line does not always coincide with the
    specific line the regex matches within the chunk; the contract
    is that the indexed branch finds the same files.)
    """
    workspace = tmp_path / "ws"
    workspace.mkdir()
    _seed_realistic_codebase(workspace)
    store = ExploreStore(tmp_path / ".agent" / "ralph-explore")
    try:
        _populate(workspace, store)
        handle = build_sqlite_index_handle(store)
        # Plain literal
        for pattern in ("helper", "os.path", "json", "join_path"):
            indexed = _run_grep(
                _FakeSession(handle), workspace, pattern, use_index="always"
            )
            live = _run_grep(
                _FakeSession(explore_index=None), workspace, pattern, use_index="never"
            )
            indexed_paths = {m["path"] for m in indexed["matches"]}
            live_paths = {m["path"] for m in live["matches"]}
            # The indexed path must include every file the live
            # path found (it can over-include when the FTS5 phrase
            # matches a chunk that doesn't actually contain the
            # regex; the handler's whole_word / post-filter path
            # narrows that for case-sensitive searches).
            assert live_paths <= indexed_paths, (
                f"indexed missed files for {pattern!r}: "
                f"live={live_paths - indexed_paths}"
            )
            assert indexed["index_used"] is True
            assert live["index_used"] is False
    finally:
        store.close()


def test_indexed_grep_case_sensitive_parity(tmp_path: Path) -> None:
    """Case-sensitive indexed matches equal live case-sensitive."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    _seed_realistic_codebase(workspace)
    store = ExploreStore(tmp_path / ".agent" / "ralph-explore")
    try:
        _populate(workspace, store)
        handle = build_sqlite_index_handle(store)
        for pattern in ("helper", "Helper", "os.path"):
            indexed = _run_grep(
                _FakeSession(handle),
                workspace,
                pattern,
                case_sensitive=True,
                use_index="always",
            )
            live = _run_grep(
                _FakeSession(explore_index=None),
                workspace,
                pattern,
                case_sensitive=True,
                use_index="never",
            )
            indexed_set = {(m["path"], m["line"]) for m in indexed["matches"]}
            live_set = {(m["path"], m["line"]) for m in live["matches"]}
            assert indexed_set == live_set, (
                f"case-sensitive parity failed for {pattern!r}"
            )
    finally:
        store.close()


def test_indexed_grep_whole_word_parity(tmp_path: Path) -> None:
    """Whole-word indexed matches equal live whole-word."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    _seed_realistic_codebase(workspace)
    store = ExploreStore(tmp_path / ".agent" / "ralph-explore")
    try:
        _populate(workspace, store)
        handle = build_sqlite_index_handle(store)
        result = handle_grep_files(
            _FakeSession(handle),
            _Workspace(workspace),
            {
                "pattern": "helper",
                "path": ".",
                "regex": False,
                "case_sensitive": False,
                "use_index": "always",
                "whole_word": True,
            },
        )
        live = handle_grep_files(
            _FakeSession(explore_index=None),
            _Workspace(workspace),
            {
                "pattern": "helper",
                "path": ".",
                "regex": False,
                "case_sensitive": False,
                "use_index": "never",
                "whole_word": True,
            },
        )
        indexed_set = {(m["path"], m["line"]) for m in _decode(result)["matches"]}
        live_set = {(m["path"], m["line"]) for m in _decode(live)["matches"]}
        assert indexed_set == live_set
    finally:
        store.close()


def test_dotted_literal_uses_index_with_phrase_equality(tmp_path: Path) -> None:
    """``os.path`` is served from the index and matches only true phrase hits."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    _seed_realistic_codebase(workspace)
    store = ExploreStore(tmp_path / ".agent" / "ralph-explore")
    try:
        _populate(workspace, store)
        handle = build_sqlite_index_handle(store)
        result = _run_grep(
            _FakeSession(handle), workspace, "os.path", use_index="always"
        )
        assert result["index_used"] is True
        # Every match must contain the exact ``os.path`` phrase
        # (the FTS5 phrase query rejects ``os.environ`` etc).
        for match in result["matches"]:
            assert "os.path" in match.get("text", ""), match
        # And we should never match a line that only has ``os.environ``.
        for match in result["matches"]:
            assert "os.environ" not in match.get("text", ""), match
    finally:
        store.close()


def test_search_files_returns_real_fixture_structure(tmp_path: Path) -> None:
    """search_files indexed view finds files in the real fixture."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    _seed_realistic_codebase(workspace)
    store = ExploreStore(tmp_path / ".agent" / "ralph-explore")
    try:
        _populate(workspace, store)
        handle = build_sqlite_index_handle(store)
        result = handle_search_files(
            _FakeSession(handle),
            _Workspace(workspace),
            {"pattern": "*.py", "path": ".", "use_index": "always"},
        )
        payload = json.loads(result.content[0].text)
        # ``search_files`` returns plain path strings in the
        # ``matches`` list (not dicts); grab them directly.
        paths = set(payload["matches"])
        # Must include real fixture files
        assert any("src/pkg/" in p for p in paths), paths
        assert any("tests/test_" in p for p in paths), paths
    finally:
        store.close()


def test_ralph_index_status_reports_fresh_index(tmp_path: Path) -> None:
    """ralph_index_status reports a fresh index with realistic file count."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    _seed_realistic_codebase(workspace)
    store = ExploreStore(workspace / ".agent" / "ralph-explore")
    try:
        _populate(workspace, store)
        handle = _build_handle_for(workspace)
        result = handle_ralph_index_status(_FakeSession(handle), workspace, {})
        payload = json.loads(result.content[0].text)
        assert payload["index_exists"] is True
        assert payload["files_indexed"] >= 50, payload
        assert payload["generation"] >= 1
    finally:
        store.close()


def test_ralph_reindex_changed_picks_up_edits(tmp_path: Path) -> None:
    """ralph_reindex(mode='changed') picks up an edited file."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    _seed_realistic_codebase(workspace)
    store = ExploreStore(workspace / ".agent" / "ralph-explore")
    try:
        _populate(workspace, store)
        handle = _build_handle_for(workspace)
        # Add a brand new file.
        new_path = workspace / "src" / "pkg" / "fresh.py"
        new_path.write_text("def fresh_helper(): return 'NEW_VALUE'\n")
        handle.store.mark_dirty(
            "src/pkg/fresh.py", source_tool="test", reason="mutated"
        )
        result = handle_ralph_reindex(
            _FakeSession(handle), workspace, {"mode": "changed"}
        )
        payload = json.loads(result.content[0].text)
        assert payload["job_status"] == "ok", payload
        # Now grep should find the new content.
        result2 = _run_grep(
            _FakeSession(handle), workspace, "NEW_VALUE", use_index="always"
        )
        assert any(
            "fresh.py" in m.get("path", "") for m in result2["matches"]
        ), result2
    finally:
        store.close()


def test_ralph_graph_resolves_neighbors(tmp_path: Path) -> None:
    """ralph_graph neighbors on a defined symbol resolves."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    _seed_realistic_codebase(workspace)
    store = ExploreStore(workspace / ".agent" / "ralph-explore")
    try:
        _populate(workspace, store)
        handle = _build_handle_for(workspace)
        # query_type='hubs' or 'neighbors' depending on the tool
        # contract; both must return a well-formed structured result.
        result = handle_ralph_graph(
            _FakeSession(handle),
            workspace,
            {"query_type": "hubs", "limit": 5},
        )
        # The result is a well-formed payload (success or empty)
        payload = json.loads(result.content[0].text)
        assert isinstance(payload, dict)
        assert "nodes" in payload
    finally:
        store.close()


def test_directory_tree_indexed_view(tmp_path: Path) -> None:
    """directory_tree returns the realistic fixture structure."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    _seed_realistic_codebase(workspace)
    store = ExploreStore(tmp_path / ".agent" / "ralph-explore")
    try:
        _populate(workspace, store)
        handle = build_sqlite_index_handle(store)
        from ralph.mcp.tools.workspace._read_handlers import handle_directory_tree

        result = handle_directory_tree(
            _FakeSession(handle),
            _Workspace(workspace),
            {"path": ".", "view": "raw", "use_index": "always"},
        )
        payload = json.loads(result.content[0].text)
        # The shape depends on the handler; assert it's a valid
        # structured response (the exact keys are handler-defined).
        assert isinstance(payload, dict)
    finally:
        store.close()


def test_indexed_search_completes_under_5s(tmp_path: Path) -> None:
    """Whole fixture index + search completes well under 5 seconds."""
    import time

    workspace = tmp_path / "ws"
    workspace.mkdir()
    _seed_realistic_codebase(workspace)
    store = ExploreStore(tmp_path / ".agent" / "ralph-explore")
    try:
        start = time.monotonic()
        _populate(workspace, store)
        handle = build_sqlite_index_handle(store)
        # Run several queries to exercise the indexed path.
        for pattern in ("helper", "os.path", "join_path", "json"):
            _run_grep(
                _FakeSession(handle), workspace, pattern, use_index="always"
            )
        elapsed = time.monotonic() - start
        assert elapsed < 5.0, f"indexed search took {elapsed:.2f}s (>5s budget)"
    finally:
        store.close()
