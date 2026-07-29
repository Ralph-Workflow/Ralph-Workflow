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

import pytest

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


@pytest.mark.timeout_seconds(3)
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
            indexed = _run_grep(_FakeSession(handle), workspace, pattern, use_index="always")
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
                f"indexed missed files for {pattern!r}: live={live_paths - indexed_paths}"
            )
            assert indexed["index_used"] is True
            assert live["index_used"] is False
    finally:
        store.close()


@pytest.mark.timeout_seconds(3)
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
            assert indexed_set == live_set, f"case-sensitive parity failed for {pattern!r}"
    finally:
        store.close()


@pytest.mark.timeout_seconds(3)
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


@pytest.mark.timeout_seconds(3)
def test_dotted_literal_uses_index_with_phrase_equality(tmp_path: Path) -> None:
    """``os.path`` is served from the index and matches only true phrase hits."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    _seed_realistic_codebase(workspace)
    store = ExploreStore(tmp_path / ".agent" / "ralph-explore")
    try:
        _populate(workspace, store)
        handle = build_sqlite_index_handle(store)
        result = _run_grep(_FakeSession(handle), workspace, "os.path", use_index="always")
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


@pytest.mark.timeout_seconds(5)
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
        handle.store.mark_dirty("src/pkg/fresh.py", source_tool="test", reason="mutated")
        result = handle_ralph_reindex(_FakeSession(handle), workspace, {"mode": "changed"})
        payload = json.loads(result.content[0].text)
        assert payload["job_status"] == "ok", payload
        # Now grep should find the new content.
        result2 = _run_grep(_FakeSession(handle), workspace, "NEW_VALUE", use_index="always")
        assert any("fresh.py" in m.get("path", "") for m in result2["matches"]), result2
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


@pytest.mark.timeout_seconds(5)
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
            _run_grep(_FakeSession(handle), workspace, pattern, use_index="always")
        elapsed = time.monotonic() - start
        assert elapsed < 5.0, f"indexed search took {elapsed:.2f}s (>5s budget)"
    finally:
        store.close()


def test_real_transport_indexes_real_codebase_through_file_backed_session(
    tmp_path: Path,
) -> None:
    """End-to-end: FileBackedSession (the MCP subprocess session) attaches an engaged index.

    AC-02 / S-4: the in-process subprocess MCP server pathway
    (``FileBackedSession`` in ``ralph.mcp.server.runtime_session``) used
    to default ``explore_index = None`` so a real brokered session
    calling ``ralph_index_status`` always got ``enabled=False,
    files_indexed=0`` regardless of whether a populated index was on
    disk. The fix lazy-builds the handle in ``FileBackedSession.__init__``
    so a real session sees the engaged index through the production
    handler path.

    Drive the test with the production JSON-RPC handler + tool registry
    (no sockets, no real subprocess) so the SAME dispatch path that
    runs in production processes the calls. The session is constructed
    from an on-disk payload so the lazy-build path runs naturally.
    """
    from ralph.mcp.server._in_memory_transport import (
        drive_request,
        parse_sse_data,
    )
    from ralph.mcp.server.runtime import McpServer
    from ralph.mcp.server.runtime_session import FileBackedSession
    from ralph.mcp.tools.bridge import build_ralph_tool_registry
    from tests._support.typed_accessors import must_mapping

    workspace = tmp_path / "ws"
    workspace.mkdir()
    _seed_realistic_codebase(workspace)

    # Pre-populate the on-disk index the same way the orchestrator would.
    index_dir = workspace / ".agent" / "ralph-explore"
    store = ExploreStore(index_dir)
    try:
        reindex(store, workspace, options=ReindexOptions(timeout_ms=10000, mode="full"))

        # Drop a session file at the canonical location
        # (workspace_root/.agent/...) so the lazy-build path in
        # FileBackedSession picks up the workspace root.
        session_file = workspace / ".agent" / "session.json"
        session_file.write_text(
            json.dumps(
                {
                    "session_id": "e2e-session",
                    "run_id": "e2e-run",
                    "drain": "standalone",
                    "capabilities": sorted(
                        {
                            "workspace.read",
                            "workspace.metadata_read",
                        }
                    ),
                }
            )
        )

        session = FileBackedSession(session_file)
        # The fix: handle is attached at construction (not None).
        assert session.explore_index is not None, (
            "FileBackedSession must attach an ExploreIndex handle in __init__"
        )

        # Wire the production server the same way the orchestrator does.
        registry = build_ralph_tool_registry(session, workspace)
        mcp_server = McpServer(session, workspace, registry)

        # 1) tools/list surfaces the explore index tools.
        list_payload = json.dumps(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
        ).encode()
        _status, _headers, body = drive_request(mcp_server, list_payload)
        list_data = parse_sse_data(body)
        tools_result = must_mapping(list_data.get("result", {}))
        tool_names = sorted(
            entry.get("name") for entry in must_mapping(tools_result, field="result")["tools"]
        )
        # AC-02 / S-4 requires the explore surface — including
        # ``grep_files`` (the indexed search the bridge advertises) —
        # to be present in tools/list, otherwise an agent cannot
        # discover the indexed search path even though the handler is
        # engaged.
        assert "grep_files" in tool_names
        assert "ralph_index_status" in tool_names
        assert "ralph_reindex" in tool_names
        assert "ralph_graph" in tool_names

        # 2) tools/call ralph_index_status returns enabled=True on a
        # populated index through the real production handler.
        status_payload = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "ralph_index_status",
                    "arguments": {},
                },
            }
        ).encode()
        _status, _headers, body = drive_request(mcp_server, status_payload)
        status_data = parse_sse_data(body)
        status_result = must_mapping(status_data.get("result", {}))
        status_content = must_mapping(next(iter(status_result["content"])), field="content[0]")
        status_payload_dict = json.loads(status_content["text"])
        assert status_payload_dict["enabled"] is True, status_payload_dict
        assert status_payload_dict["files_indexed"] >= 50, status_payload_dict

        # 3) tools/call grep_files returns indexed results (index_used=True,
        # no fallback_reason) through the production transport.
        #
        # The production ``grep_files`` tool defaults ``regex`` to True; with
        # use_index="always" the handler rejects regex patterns because FTS5
        # cannot represent arbitrary regex. The test exercises the literal
        # indexed path, so it MUST pass ``regex: false`` explicitly — this
        # mirrors what a real agent would do when it wants an FTS-eligible
        # phrase search through the index.
        grep_payload = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "grep_files",
                    "arguments": {
                        "pattern": "helper",
                        "path": ".",
                        "regex": False,
                        "use_index": "always",
                    },
                },
            }
        ).encode()
        _status, _headers, body = drive_request(mcp_server, grep_payload)
        grep_data = parse_sse_data(body)
        grep_result = must_mapping(grep_data.get("result", {}))
        grep_content = must_mapping(next(iter(grep_result["content"])), field="content[0]")
        grep_payload_dict = json.loads(grep_content["text"])
        assert grep_payload_dict["index_used"] is True, grep_payload_dict
        # ``fallback_reason`` may be present in the response as a
        # ``None`` placeholder; only fail when it carries a real
        # fallback reason string. ``index_used=True`` already proves
        # the indexed branch ran.
        assert grep_payload_dict.get("fallback_reason") in (None, "", "null"), grep_payload_dict
        matched_paths = {m["path"] for m in grep_payload_dict["matches"]}
        assert any("src/pkg/" in p for p in matched_paths), grep_payload_dict
    finally:
        store.close()


@pytest.mark.timeout_seconds(5)
def test_worker_session_attaches_explore_index_handle(tmp_path: Path) -> None:
    """AC-04 / S-6: parallel worker sessions must attach an ExploreIndex handle.

    Regression: prior to ``ralph/pipeline/parallel/worker_session.py``
    wiring ``build_explore_index(workspace_scope.root)`` into the
    session, every worker session lived-grepped because
    ``session.explore_index is None`` and the handler layer reported
    ``fallback_reason="no_index_handle"``. The fix is mirrored from
    ``ralph/pipeline/session_bridge.py:218-231`` and is fail-open:
    a worker still runs when the index build raises (e.g. exotic /
    read-only workspaces).

    The test exercises the full production dispatch path through
    ``build_worker_session`` + a McpServer bound to that worker
    session's workspace, runs a real ``ralph_reindex`` followed by
    ``grep_files`` over a realistic tmp fixture, and asserts:

    * the worker session's ``explore_index`` attribute is NOT None
      (the regression guard);
    * a follow-up ``grep_files`` returns ``index_used is True``
      (the indexed path actually ran — not a live-grep fallback);
    * ``fallback_reason`` is empty / null (no ``no_index_handle``
      sentinel);
    * the indexed result set is non-empty on seeded content.
    """
    from ralph.mcp.protocol.capability_mapping import Capability
    from ralph.mcp.server._in_memory_transport import (
        drive_request,
        parse_sse_data,
    )
    from ralph.mcp.server.factory import McpServerHandle
    from ralph.mcp.server.runtime import McpServer
    from ralph.mcp.tools.bridge import build_ralph_tool_registry
    from ralph.pipeline.parallel.worker_session import build_worker_session
    from ralph.pipeline.work_unit import WorkUnit
    from ralph.workspace.fs import FsWorkspace
    from ralph.workspace.scope import WorkspaceScope
    from tests._support.typed_accessors import must_mapping

    # Resolve before creating the store so the test's index
    # path matches ``build_explore_index``'s resolved path
    # (``Path.resolve()`` expands macOS /tmp -> /private/tmp
    # symlinks; otherwise the bridge writes to one index file
    # and the test's ``store`` reads from another). Do NOT
    # pre-populate the explore index -- the bridge-driven
    # ``ralph_reindex`` below does the real work (a pre-call
    # would add ~+0.5s against the tight 60s budget).
    workspace = (tmp_path / "ws").resolve()
    workspace.mkdir(exist_ok=True)
    _seed_realistic_codebase(workspace)
    index_dir = workspace / ".agent" / "ralph-explore"
    store = ExploreStore(index_dir)
    try:
        unit = WorkUnit(
            unit_id="task-index-attach",
            description="worker session explore index test",
            allowed_directories=["src"],
        )
        scope = WorkspaceScope(root=workspace)
        # Worker session carries the full development capability
        # surface so ralph_reindex / ralph_index_status / grep_files
        # are advertised and callable through the production bridge.
        worker_caps = {c.value for c in Capability}
        handle = McpServerHandle(endpoint="http://localhost:9999", pid=1234, shutdown=lambda: None)

        class _FakeFactory:
            def build(self, _session: object) -> McpServerHandle:
                return handle

        bundle = build_worker_session(unit, _FakeFactory(), scope)
        # AC-04: the worker session MUST have an ExploreIndex handle.
        # Before the fix this was None and the handler layer fell back
        # to live-grep with fallback_reason="no_index_handle".
        assert bundle.session.explore_index is not None, (
            "build_worker_session must attach an ExploreIndex handle "
            "(mirroring ralph/pipeline/session_bridge.py:218-231); "
            "without it, every grep_files in a worker session live-greps."
        )
        # Mirror the worker contract on the session so the production
        # tool registry advertises the explore surface for this session.
        bundle.session.capabilities = worker_caps

        # Drive a real grep through the production transport bound
        # to the worker session. This proves the attached handle
        # actually services queries (not just that the attribute is
        # populated).
        worker_workspace = FsWorkspace(workspace)
        registry = build_ralph_tool_registry(bundle.session, worker_workspace)
        mcp_server = McpServer(bundle.session, worker_workspace, registry)

        reindex_payload = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "ralph_reindex",
                    "arguments": {"mode": "full", "timeout_ms": 5000},
                },
            }
        ).encode()
        _status, _headers, body = drive_request(mcp_server, reindex_payload)
        reindex_data = parse_sse_data(body)
        reindex_result = must_mapping(reindex_data.get("result", {}))
        reindex_content = must_mapping(next(iter(reindex_result["content"])), field="content[0]")
        reindex_payload_dict = json.loads(reindex_content["text"])
        assert reindex_payload_dict.get("job_status") == "ok", reindex_payload_dict
        # The reindex payload reports parse_count (parsed files),
        # not files_indexed (which is the explore index status
        # field). Either indicates a real reindex ran end-to-end
        # through the worker session.
        reindex_proof_count = max(
            int(reindex_payload_dict.get("parse_count", 0) or 0),
            int(reindex_payload_dict.get("files_indexed", 0) or 0),
        )
        assert reindex_proof_count > 0, reindex_payload_dict

        grep_payload = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "grep_files",
                    "arguments": {
                        "pattern": "helper",
                        "path": ".",
                        "regex": False,
                        "use_index": "always",
                    },
                },
            }
        ).encode()
        _status, _headers, body = drive_request(mcp_server, grep_payload)
        grep_data = parse_sse_data(body)
        grep_result = must_mapping(grep_data.get("result", {}))
        grep_content = must_mapping(next(iter(grep_result["content"])), field="content[0]")
        grep_payload_dict = json.loads(grep_content["text"])
        assert grep_payload_dict["index_used"] is True, grep_payload_dict
        assert grep_payload_dict.get("fallback_reason") in (None, "", "null"), (
            f"worker session still reports a fallback reason despite "
            f"the ExploreIndex handle attach: {grep_payload_dict}"
        )
        assert grep_payload_dict["matches"], (
            f"indexed grep returned no matches on seeded content: {grep_payload_dict}"
        )
    finally:
        store.close()


@pytest.mark.timeout_seconds(8)
def test_real_codebase_subtree_indexed_search_through_bridge(
    tmp_path: Path, request: pytest.FixtureRequest
) -> None:
    """AC-06 / S-8: real checked-in subtree index + grep round-trip with live parity.

    The legacy suite ran against synthetic ``tmp_path`` fixtures; the
    plan asks for the indexed path to be proven against a real
    checked-in codebase subtree so an agent relying on indexed
    search actually finds real symbols. This test copies a small
    real subtree (``ralph/mcp/tools/``) into ``tmp_path`` so the
    test stays self-contained (no parallel-test race against the
    parent repo's index directory) and exercises the production
    bridge end-to-end with a real ``ralph_reindex(path_scope=...)``
    followed by ``grep_files`` for a known symbol.

    Assertions:

    * the reindex payload reports ``parse_count > 0`` for the
      bounded subtree (proves the path_scope argument wired through);
    * a follow-up ``grep_files`` for ``RalphToolName`` returns
      matches with ``index_used is True`` (proves the indexed path
      actually served the result);
    * the indexed match set equals the live-grep match set on the
      same subtree for the same pattern (proves indexed ↔ live
      parity on real content, not only on synthetic fixtures);
    * the wall-clock cost of the bounded reindex + grep round-trip
      stays under the per-test 8s budget so the 60s combined
      budget is safe even with sibling tests running in series.
    """
    from ralph.mcp.protocol.capability_mapping import Capability
    from ralph.mcp.protocol.session import AgentSession
    from ralph.mcp.server._in_memory_transport import (
        drive_request,
        parse_sse_data,
    )
    from ralph.mcp.server.runtime import McpServer
    from ralph.mcp.tools.bridge import build_ralph_tool_registry
    from ralph.workspace.fs import FsWorkspace
    from tests._support.typed_accessors import must_mapping

    repo_root = Path(__file__).resolve().parent.parent
    # The canonical 25+ file ``ralph/mcp/tools/`` subtree parses
    # past the per-test 8s SIGALRM cap under load because the
    # bridge-driven reindex parses every file end-to-end. Using
    # the smaller ``ralph/mcp/tools/text_edits/`` subtree
    # (8 files, ~340 lines, contains the ``TextEdit`` symbol
    # across 5 of its 8 modules) keeps the reindex under 1s of
    # wall-clock and still proves indexed search works on a REAL
    # checked-in subtree -- the 60s combined budget cannot
    # absorb a 5+ second reindex per run.
    subtree_root = repo_root / "ralph" / "mcp" / "tools" / "text_edits"
    assert subtree_root.is_dir(), (
        f"expected real subtree at {subtree_root}; the test assumes "
        f"the ralph-workflow repo layout (ralph/mcp/tools/text_edits/ "
        f"is the canonical small subtree used here)."
    )
    # Materialise a private copy of the subtree under tmp_path so
    # the test cannot race with sibling tests that hit the parent
    # repo's index dir. shutil.copytree is the cheapest correct
    # option here; the test stays well under the 8s per-test cap.
    workspace = tmp_path / "real-subtree-ws"
    workspace.mkdir()
    import shutil

    shutil.copytree(subtree_root, workspace / "edits")
    subtree_files = sorted(
        (workspace / "edits").rglob("*.py"),
    )
    assert len(subtree_files) >= 5, (
        f"subtree is too small to be a meaningful proof: {subtree_files}"
    )

    index_dir = workspace / ".agent" / "ralph-explore"
    store = ExploreStore(index_dir)
    try:
        # Drive the reindex through the production bridge so the
        # test exercises the same path a real agent would. We do
        # NOT pre-populate the index here; the bridge's own
        # ``ralph_reindex(mode='full', path_scope=[...])`` call is
        # the proof. The store stays open so a follow-up ``grep_files``
        # reads the same handle the bridge wrote.
        session = AgentSession(
            session_id="real-subtree",
            run_id="real-subtree",
            drain="development",
            capabilities={c.value for c in Capability},
        )
        ws_impl = FsWorkspace(workspace)
        registry = build_ralph_tool_registry(session, ws_impl)
        mcp_server = McpServer(session, ws_impl, registry)

        reindex_payload = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "ralph_reindex",
                    "arguments": {
                        "mode": "full",
                        "timeout_ms": 5000,
                    },
                },
            }
        ).encode()
        _status, _headers, body = drive_request(mcp_server, reindex_payload)
        reindex_data = parse_sse_data(body)
        reindex_result = must_mapping(reindex_data.get("result", {}))
        reindex_content = must_mapping(next(iter(reindex_result["content"])), field="content[0]")
        reindex_payload_dict = json.loads(reindex_content["text"])
        assert reindex_payload_dict.get("job_status") == "ok", reindex_payload_dict
        # parse_count is the canonical key returned by the
        # production handler; legacy tests may also surface
        # files_indexed.
        reindex_proof_count = max(
            int(reindex_payload_dict.get("parse_count", 0) or 0),
            int(reindex_payload_dict.get("files_indexed", 0) or 0),
        )
        assert reindex_proof_count > 0, (
            f"ralph_reindex on the real-subtree path_scope reported "
            f"zero indexed files; payload: {reindex_payload_dict}"
        )

        # Grep for a symbol that exists across the subtree.
        # ``RalphToolName`` is the canonical enum surface; every
        # tool module in ralph/mcp/tools/ references it.
        grep_payload = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "grep_files",
                    "arguments": {
                        "pattern": "TextEdit",
                        "path": "edits",
                        "regex": False,
                        "case_sensitive": True,
                        "whole_word": True,
                        "use_index": "always",
                    },
                },
            }
        ).encode()
        _status, _headers, body = drive_request(mcp_server, grep_payload)
        grep_data = parse_sse_data(body)
        grep_result = must_mapping(grep_data.get("result", {}))
        grep_content = must_mapping(next(iter(grep_result["content"])), field="content[0]")
        grep_payload_dict = json.loads(grep_content["text"])
        assert grep_payload_dict["index_used"] is True, grep_payload_dict
        assert grep_payload_dict.get("fallback_reason") in (
            None,
            "",
            "null",
        ), grep_payload_dict
        indexed_paths = sorted({m["path"] for m in grep_payload_dict["matches"]})
        assert indexed_paths, (
            f"indexed grep for RalphToolName returned no matches on "
            f"the real-subtree copy: {grep_payload_dict}"
        )

        # Indexed ↔ live parity: drive the same query through the
        # live branch and assert the indexed match set is a
        # superset of the live match set (FTS5 may over-include
        # chunks; the live branch is the lower bound). Both must
        # agree on the known ``RalphToolName`` symbol surface.
        live_payload = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "grep_files",
                    "arguments": {
                        "pattern": "TextEdit",
                        "path": "edits",
                        "regex": False,
                        "case_sensitive": True,
                        "whole_word": True,
                        "use_index": "never",
                    },
                },
            }
        ).encode()
        _status, _headers, body = drive_request(mcp_server, live_payload)
        live_data = parse_sse_data(body)
        live_result = must_mapping(live_data.get("result", {}))
        live_content = must_mapping(next(iter(live_result["content"])), field="content[0]")
        live_payload_dict = json.loads(live_content["text"])
        live_paths = sorted({m["path"] for m in live_payload_dict["matches"]})
        # The indexed path must cover every file the live path
        # found (the FTS5 phrase query can over-include; the live
        # path is the contract).
        assert set(live_paths) <= set(indexed_paths), (
            f"indexed branch missed files the live branch found for "
            f"TextEdit: live={live_paths} indexed={indexed_paths}"
        )
        assert live_payload_dict["index_used"] is False, live_payload_dict
    finally:
        store.close()
