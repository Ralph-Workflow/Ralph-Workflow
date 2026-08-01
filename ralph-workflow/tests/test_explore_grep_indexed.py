"""Black-box tests for indexed ``grep_files`` behavior."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ralph.mcp.explore.dirty_paths import build_sqlite_index_handle
from ralph.mcp.explore.pipeline import ReindexOptions, reindex
from ralph.mcp.explore.ranking import (
    is_fts_eligible,
    score_search_file,
    sort_ranked,
)
from ralph.mcp.explore.store import (
    ExploreStore,
)
from ralph.mcp.tools.workspace._grep_handlers import handle_grep_files


class _FakeSession:
    def __init__(self, explore_index=None):
        self.explore_index = explore_index

    def check_capability(self, capability: str):
        return {"status": "approved", "capability": capability}

    def check_edit_area(self, path: str):
        return {"status": "approved", "path": path}


class _Workspace:
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

    def iter_files(self, base: str):
        base_path = self.root / base if base else self.root
        for path in base_path.rglob("*"):
            if path.is_file():
                yield str(path.relative_to(self.root))

    def list_dir(self, base: str):
        target = self.root / base if base else self.root
        return [p.name for p in target.iterdir()]


def _seed_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "hello.py").write_text("def hello():\n    return 'world'\n")
    (workspace / "goodbye.py").write_text("def goodbye():\n    return 'farewell'\n")
    return workspace


def _decode(result) -> dict:
    return json.loads(result.content[0].text)


def _populate_index(workspace: Path, store: ExploreStore) -> None:
    """Run a cold build so the index has rows for the seeded workspace."""
    reindex(store, workspace, options=ReindexOptions(timeout_ms=5000))


def test_is_fts_eligible_accepts_literal() -> None:
    assert is_fts_eligible("hello", is_regex=False, whole_word=False) is True
    assert is_fts_eligible("hello world", is_regex=False, whole_word=False) is True


def test_is_fts_eligible_rejects_regex_metachars() -> None:
    assert is_fts_eligible("foo.*", is_regex=False, whole_word=False) is False
    assert is_fts_eligible("foo.*", is_regex=True, whole_word=False) is False


def test_is_fts_eligible_rejects_whole_word_phrase() -> None:
    assert is_fts_eligible("hello world", is_regex=False, whole_word=True) is False


def test_is_fts_eligible_rejects_empty() -> None:
    assert is_fts_eligible("", is_regex=False, whole_word=False) is False


def test_indexed_grep_returns_compact_match(tmp_path: Path) -> None:
    workspace = _seed_workspace(tmp_path)
    store = ExploreStore(tmp_path / ".agent" / "ralph-explore")
    try:
        _populate_index(workspace, store)
        session = _FakeSession(build_sqlite_index_handle(store))
        result = handle_grep_files(
            session,
            _Workspace(workspace),
            {
                "pattern": "hello",
                "path": ".",
                "regex": False,
                "case_sensitive": False,
                "use_index": "auto",
                "return_evidence_ids": True,
            },
        )
        payload = _decode(result)
        assert payload["index_used"] is True
        assert payload["ranked_by"] == "match"
        assert any("hello" in (m.get("text") or "") for m in payload["matches"])
        # Evidence ids should be present.
        for ev_id in payload["evidence_ids"]:
            assert isinstance(ev_id, str)
            assert len(ev_id) > 0
    finally:
        store.close()


def test_indexed_grep_falls_back_to_live_for_regex(tmp_path: Path) -> None:
    workspace = _seed_workspace(tmp_path)
    store = ExploreStore(tmp_path / ".agent" / "ralph-explore")
    try:
        _populate_index(workspace, store)
        session = _FakeSession(build_sqlite_index_handle(store))
        result = handle_grep_files(
            session,
            _Workspace(workspace),
            {
                "pattern": "h.llo",
                "path": ".",
                "regex": True,
                "use_index": "auto",
            },
        )
        payload = _decode(result)
        # Live grep ran because FTS cannot represent ``.``.
        assert payload["index_used"] is False
        assert payload["fallback_reason"] == "pattern_not_fts_eligible"
        assert any(
            "h.llo" in (m.get("text") or "") or "hello" in (m.get("text") or "")
            for m in payload["matches"]
        )
    finally:
        store.close()


def test_indexed_grep_use_index_always_fails_closed_for_regex(tmp_path: Path) -> None:
    workspace = _seed_workspace(tmp_path)
    store = ExploreStore(tmp_path / ".agent" / "ralph-explore")
    try:
        _populate_index(workspace, store)
        session = _FakeSession(build_sqlite_index_handle(store))
        from ralph.mcp.tools.coordination import InvalidParamsError

        with pytest.raises(InvalidParamsError):
            handle_grep_files(
                session,
                _Workspace(workspace),
                {
                    "pattern": "h.llo",
                    "path": ".",
                    "regex": True,
                    "use_index": "always",
                },
            )
    finally:
        store.close()


def test_indexed_grep_use_index_never_preserves_legacy_output(tmp_path: Path) -> None:
    workspace = _seed_workspace(tmp_path)
    store = ExploreStore(tmp_path / ".agent" / "ralph-explore")
    try:
        _populate_index(workspace, store)
        session = _FakeSession(build_sqlite_index_handle(store))
        result = handle_grep_files(
            session,
            _Workspace(workspace),
            {
                "pattern": "hello",
                "path": ".",
                "regex": False,
                "case_sensitive": False,
                "use_index": "never",
            },
        )
        payload = _decode(result)
        assert payload["index_used"] is False
        # Legacy live output shape (no indexed block).
        assert "matches" in payload
    finally:
        store.close()


def test_indexed_grep_returns_score_reasons(tmp_path: Path) -> None:
    workspace = _seed_workspace(tmp_path)
    store = ExploreStore(tmp_path / ".agent" / "ralph-explore")
    try:
        _populate_index(workspace, store)
        session = _FakeSession(build_sqlite_index_handle(store))
        result = handle_grep_files(
            session,
            _Workspace(workspace),
            {
                "pattern": "hello",
                "path": ".",
                "regex": False,
                "case_sensitive": False,
                "use_index": "auto",
                "rank_by": "hybrid",
            },
        )
        payload = _decode(result)
        # Phase 1 hybrid = match baseline; the response includes a
        # score_reasons array. Empty list is acceptable when there
        # are no ranked items (no git-changed signal in this test).
        assert "score_reasons" in payload
    finally:
        store.close()


def test_indexed_grep_without_index_handle_falls_back(tmp_path: Path) -> None:
    workspace = _seed_workspace(tmp_path)
    session = _FakeSession(explore_index=None)
    result = handle_grep_files(
        session,
        _Workspace(workspace),
        {
            "pattern": "hello",
            "path": ".",
            "regex": False,
            "case_sensitive": False,
            "use_index": "auto",
        },
    )
    payload = _decode(result)
    # No index handle -> live grep runs.
    assert payload["index_used"] is False
    assert payload["fallback_reason"] == "no_index_handle"


def test_score_search_file_exact_basename_outranks_subdir(tmp_path: Path) -> None:
    candidate_path = "tools/foo.py"
    item = score_search_file(
        candidate_path=candidate_path,
        basename="foo.py",
        role_requested=None,
        is_git_changed=False,
    )
    assert item.score > 0
    # Subdirectory match should NOT add exact-basename.
    other = score_search_file(
        candidate_path="deep/nested/foo.py",
        basename="bar.py",
        role_requested=None,
        is_git_changed=False,
    )
    assert other.score == 0


def test_score_search_file_git_changed_adds_bonus() -> None:
    candidate_path = "tools/foo.py"
    item = score_search_file(
        candidate_path=candidate_path,
        basename="foo.py",
        role_requested=None,
        is_git_changed=True,
    )
    assert any("git_changed_path" in reason for reason in item.reasons)


def test_score_search_file_generated_penalty() -> None:
    candidate_path = "vendor/lib/foo.py"
    item = score_search_file(
        candidate_path=candidate_path,
        basename="foo.py",
        role_requested=None,
        is_git_changed=False,
    )
    assert any("generated" in reason for reason in item.reasons)
    # The -50 penalty is applied; the exact-basename +100 wins out
    # so the net is +50 but the reason line documents the penalty.
    assert item.score == 50


def test_sort_ranked_is_stable_by_path_line_evidence() -> None:
    from ralph.mcp.explore.ranking import RankedItem

    a = RankedItem(key="a", score=10, reasons=(), path="b.py", line=1, evidence_id="ev1")
    b = RankedItem(key="b", score=10, reasons=(), path="a.py", line=1, evidence_id="ev1")
    items = sort_ranked([a, b])
    assert items[0] is b  # a.py comes before b.py lexicographically


def test_indexed_grep_invalid_use_index_rejected(tmp_path: Path) -> None:
    workspace = _seed_workspace(tmp_path)
    session = _FakeSession(explore_index=None)
    from ralph.mcp.tools.coordination import InvalidParamsError

    with pytest.raises(InvalidParamsError):
        handle_grep_files(
            session,
            _Workspace(workspace),
            {
                "pattern": "hello",
                "path": ".",
                "use_index": "bogus",
            },
        )


def test_indexed_grep_invalid_rank_by_rejected(tmp_path: Path) -> None:
    workspace = _seed_workspace(tmp_path)
    session = _FakeSession(explore_index=None)
    from ralph.mcp.tools.coordination import InvalidParamsError

    with pytest.raises(InvalidParamsError):
        handle_grep_files(
            session,
            _Workspace(workspace),
            {
                "pattern": "hello",
                "path": ".",
                "rank_by": "bogus",
            },
        )


def test_indexed_grep_path_filter_excludes_other_dirs(tmp_path: Path) -> None:
    """AC-02: indexed grep with path='src' must not return tests/ matches."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    src = workspace / "src"
    tests = workspace / "tests"
    src.mkdir()
    tests.mkdir()
    (src / "a.py").write_text("def hello():\n    return 'a'\n")
    (tests / "b.py").write_text("def hello():\n    return 'b'\n")
    store = ExploreStore(tmp_path / ".agent" / "ralph-explore")
    try:
        _populate_index(workspace, store)
        session = _FakeSession(build_sqlite_index_handle(store))
        result = handle_grep_files(
            session,
            _Workspace(workspace),
            {
                "pattern": "hello",
                "path": "src",
                "regex": False,
                "case_sensitive": False,
                "use_index": "always",
                "return_evidence_ids": True,
            },
        )
        payload = _decode(result)
        assert payload["index_used"] is True
        paths = {m.get("path", "") for m in payload["matches"]}
        assert "src/a.py" in paths, paths
        assert "tests/b.py" not in paths, (
            f"indexed grep leaked out-of-scope tests/ match: {paths!r}"
        )
    finally:
        store.close()


def test_indexed_grep_include_exclude_globs_filter_matches(tmp_path: Path) -> None:
    """AC-02: include/exclude globs push into the indexed query."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "keep.py").write_text("def hello():\n    return 1\n")
    (workspace / "skip.py").write_text("def hello():\n    return 2\n")
    store = ExploreStore(tmp_path / ".agent" / "ralph-explore")
    try:
        _populate_index(workspace, store)
        session = _FakeSession(build_sqlite_index_handle(store))
        result = handle_grep_files(
            session,
            _Workspace(workspace),
            {
                "pattern": "hello",
                "path": ".",
                "regex": False,
                "case_sensitive": False,
                "use_index": "always",
                "include": ["keep.py"],
                "return_evidence_ids": True,
            },
        )
        payload = _decode(result)
        paths = {m.get("path", "") for m in payload["matches"]}
        assert "keep.py" in paths, paths
        assert "skip.py" not in paths, paths
    finally:
        store.close()


def test_indexed_grep_returns_bounded_evidence_backed_graph_context(
    tmp_path: Path,
) -> None:
    """Indexed graph context is bounded and references returned evidence."""
    workspace = _seed_workspace(tmp_path)
    store = ExploreStore(tmp_path / ".agent" / "ralph-explore")
    try:
        _populate_index(workspace, store)
        result = handle_grep_files(
            _FakeSession(build_sqlite_index_handle(store)),
            _Workspace(workspace),
            {
                "pattern": "hello",
                "path": ".",
                "regex": False,
                "case_sensitive": False,
                "use_index": "always",
                "include_graph_context": True,
                "return_evidence_ids": True,
                "limit": 1,
            },
        )
        payload = _decode(result)
        context = payload["graph_context"]
        assert len(context) == 1
        assert context[0]["evidence_id"] in payload["evidence_ids"]
        assert context[0]["path"] == "hello.py"
        assert context[0]["start_line"] <= context[0]["end_line"]
    finally:
        store.close()


def test_indexed_symbol_ranking_reorders_definition_ahead_of_plain_text(
    tmp_path: Path,
) -> None:
    """Distinct indexed symbol scores produce observable handler ordering."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "definition.py").write_text("def hello():\n    return 1\n")
    (workspace / "notes.md").write_text("hello appears in prose only\n")
    store = ExploreStore(tmp_path / ".agent" / "ralph-explore")
    try:
        _populate_index(workspace, store)
        result = handle_grep_files(
            _FakeSession(build_sqlite_index_handle(store)),
            _Workspace(workspace),
            {
                "pattern": "hello",
                "path": ".",
                "regex": False,
                "case_sensitive": False,
                "use_index": "always",
                "rank_by": "symbol",
                "return_evidence_ids": False,
            },
        )
        payload = _decode(result)
        assert payload["matches"][0]["path"] == "definition.py"
        assert "evidence_id" not in payload["matches"][0]
        assert payload["score_reasons"][0][0] == "+1 bare_match"
    finally:
        store.close()


# --- Defect D1: case-sensitive defaults use index (narrow + post-filter) ---


def test_indexed_grep_case_sensitive_default_uses_index_with_post_filter(
    tmp_path: Path,
) -> None:
    """AC-01/D1: case-sensitive literal (the default) must use the index.

    The FTS5 ``unicode61`` tokenizer is case-INsensitive, so the
    handler narrows candidates via FTS and re-applies a
    case-sensitive regex post-filter. Result sets must equal live
    grep's case-sensitive parity.
    """
    workspace = _seed_workspace(tmp_path)
    (workspace / "upper.py").write_text("FooBar = 'capitalized'\n")
    (workspace / "lower.py").write_text("foobar = 'lowercase'\n")
    store = ExploreStore(tmp_path / ".agent" / "ralph-explore")
    try:
        _populate_index(workspace, store)
        session = _FakeSession(build_sqlite_index_handle(store))
        result = handle_grep_files(
            session,
            _Workspace(workspace),
            {
                "pattern": "FooBar",
                "path": ".",
                "regex": False,
                "case_sensitive": True,
                "use_index": "auto",
            },
        )
        payload = _decode(result)
        assert payload["index_used"] is True, payload
        paths = {m["path"] for m in payload["matches"]}
        assert "upper.py" in paths
        assert "lower.py" not in paths
    finally:
        store.close()


def test_indexed_grep_case_sensitive_parity_with_live(tmp_path: Path) -> None:
    """AC-01: indexed case-sensitive match set equals live grep's."""
    workspace = _seed_workspace(tmp_path)
    files = {
        "a.py": "FooBar = 'capitalized'\n",
        "b.py": "no match here\n",
        "c.py": "FooBar = 'again'\n",
        "d.py": "foobar = 'lowercase'\n",
    }
    for name, content in files.items():
        (workspace / name).write_text(content)
    store = ExploreStore(tmp_path / ".agent" / "ralph-explore")
    try:
        _populate_index(workspace, store)
        handle = build_sqlite_index_handle(store)
        indexed = _decode(
            handle_grep_files(
                _FakeSession(handle),
                _Workspace(workspace),
                {
                    "pattern": "FooBar",
                    "path": ".",
                    "regex": False,
                    "case_sensitive": True,
                    "use_index": "auto",
                },
            )
        )
        live = _decode(
            handle_grep_files(
                _FakeSession(explore_index=None),
                _Workspace(workspace),
                {
                    "pattern": "FooBar",
                    "path": ".",
                    "regex": False,
                    "case_sensitive": True,
                    "use_index": "auto",
                },
            )
        )
        indexed_paths = {(m["path"], m["line"]) for m in indexed["matches"]}
        live_paths = {(m["path"], m["line"]) for m in live["matches"]}
        assert indexed_paths == live_paths
        assert indexed["index_used"] is True
        assert live["index_used"] is False
    finally:
        store.close()


# --- Defect D2: cold index falls back to live grep ---


def test_indexed_grep_cold_store_falls_back_to_live(tmp_path: Path) -> None:
    """AC-01/D2: never-reindexed store falls back to live grep with the
    correct fallback_reason instead of returning 0 matches with index_used=true.
    """
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "a.py").write_text("hello world\n")
    store = ExploreStore(tmp_path / ".agent" / "ralph-explore")
    try:
        # Do NOT reindex — current_generation stays 0.
        session = _FakeSession(build_sqlite_index_handle(store))
        result = handle_grep_files(
            session,
            _Workspace(workspace),
            {
                "pattern": "hello",
                "path": ".",
                "regex": False,
                "case_sensitive": False,
                "use_index": "auto",
            },
        )
        payload = _decode(result)
        assert payload["index_used"] is False, payload
        assert payload["fallback_reason"] == "no_committed_generation"
        # Live fallback still returned the match.
        assert any("hello" in (m.get("text") or "") for m in payload["matches"])
    finally:
        store.close()


# --- Defect D3: dotted literals served from index ---


def test_indexed_grep_dotted_literal_uses_index(tmp_path: Path) -> None:
    """AC-01/D3: dotted literals (e.g. ``os.path``) are served from the
    index via a quoted-phrase FTS query. Live-grep parity required.
    """
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "a.py").write_text("os.path.join('x')\n")
    (workspace / "b.py").write_text("os.environ['HOME']\n")
    (workspace / "c.py").write_text("unrelated line\n")
    store = ExploreStore(tmp_path / ".agent" / "ralph-explore")
    try:
        _populate_index(workspace, store)
        session = _FakeSession(build_sqlite_index_handle(store))
        result = handle_grep_files(
            session,
            _Workspace(workspace),
            {
                "pattern": "os.path",
                "path": ".",
                "regex": False,
                "case_sensitive": False,
                "use_index": "auto",
            },
        )
        payload = _decode(result)
        assert payload["index_used"] is True, payload
        paths = {m["path"] for m in payload["matches"]}
        assert "a.py" in paths
        assert "b.py" not in paths  # FTS phrase must NOT match os.environ
        assert "c.py" not in paths
    finally:
        store.close()


# --- Defect D4: stale_paths_count populated ---


def test_indexed_grep_freshness_reports_stale_paths_count(tmp_path: Path) -> None:
    """AC-01/D4: stale_paths_count mirrors the dirty-path queue, not 0."""
    workspace = _seed_workspace(tmp_path)
    store = ExploreStore(tmp_path / ".agent" / "ralph-explore")
    try:
        _populate_index(workspace, store)
        # Mark a path dirty via the store's lifecycle hook.
        handle = build_sqlite_index_handle(store)
        handle.mark_dirty(["hello.py"], source_tool="test")
        result = handle_grep_files(
            _FakeSession(handle),
            _Workspace(workspace),
            {
                "pattern": "hello",
                "path": ".",
                "regex": False,
                "case_sensitive": False,
                "use_index": "auto",
            },
        )
        payload = _decode(result)
        assert payload["stale_paths_count"] >= 1, payload
        assert payload["dirty_paths_count"] >= 1
        assert payload["is_stale"] is True
    finally:
        store.close()


# --- Defect R6: use_index="always" still fail-closes on regex ---


def test_indexed_grep_use_index_always_regex_still_fails_closed(tmp_path: Path) -> None:
    """AC-01: regex pattern with ``use_index='always'`` still raises."""
    workspace = _seed_workspace(tmp_path)
    store = ExploreStore(tmp_path / ".agent" / "ralph-explore")
    try:
        _populate_index(workspace, store)
        session = _FakeSession(build_sqlite_index_handle(store))
        from ralph.mcp.tools.coordination import InvalidParamsError

        with pytest.raises(InvalidParamsError):
            handle_grep_files(
                session,
                _Workspace(workspace),
                {
                    "pattern": "h.llo",
                    "path": ".",
                    "regex": True,
                    "use_index": "always",
                },
            )
    finally:
        store.close()
