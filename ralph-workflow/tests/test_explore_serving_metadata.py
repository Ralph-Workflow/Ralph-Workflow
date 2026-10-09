"""Black-box tests for the canonical serving metadata contract (S-2).

The ``PRODUCT_CRITERIA.md`` R1 contract requires every index-capable
tool response to carry the same machine-readable fields:

* ``index_used`` — ``True`` / ``False``
* ``fallback_reason`` — one of the canonical reason codes from
  ``docs/agents/explore-index-fault-matrix.md``, or ``None``
* ``index_staleness`` — ``{stale_paths_count, last_refresh_age, recovery_willfallback}``

These tests exercise every one of the nine index-capable tools
(``grep_files``, ``search_files``, ``read_file``,
``read_multiple_files``, ``list_directory``, ``directory_tree``,
``ralph_graph``, ``ralph_index_status``, ``ralph_reindex``) and
assert the canonical block is present, the reason code is from the
closed set, and unknown reason codes raise at the boundary so a
typo cannot leak into the public surface.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ralph.mcp.explore.handlers import (
    ExploreIndex,
    handle_ralph_graph,
    handle_ralph_index_status,
    handle_ralph_reindex,
)
from ralph.mcp.explore.pipeline import ReindexOptions, reindex
from ralph.mcp.explore.serving import (
    CANONICAL_REASON_CODES,
    serving_metadata,
)
from ralph.mcp.explore.store import ExploreStore
from ralph.mcp.tools.workspace._grep_handlers import handle_grep_files
from ralph.mcp.tools.workspace._read_handlers import (
    handle_directory_tree,
    handle_list_directory,
    handle_read_file,
    handle_read_multiple_files,
    handle_search_files,
)

# --- shared fixtures -------------------------------------------------------


class _FakeSession:
    """Minimal coordination-session seam for the serving-metadata tests."""

    session_id = "serving-meta"
    run_id = "serving-meta"
    broker_secret: str | None = None

    def __init__(self, explore_index=None) -> None:
        self.explore_index = explore_index

    def check_capability(self, capability: str):
        return {"status": "approved", "capability": capability}

    def check_edit_area(self, path: str):
        return {"status": "approved", "path": path}


class _Workspace:
    """Minimal in-memory ``Workspace`` adapter."""

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

    def read_lines(self, path: str, *, start=None, end=None, head=None, tail=None):
        text = self.read(path)
        lines = text.splitlines(keepends=False)
        total_lines = len(lines)
        if head is not None:
            sliced = lines[:head]
            return (
                "\n".join(sliced),
                {"total_lines": total_lines, "returned_lines": len(sliced), "truncated": False},
            )
        if tail is not None:
            sliced = lines[-tail:]
            return (
                "\n".join(sliced),
                {"total_lines": total_lines, "returned_lines": len(sliced), "truncated": False},
            )
        if start is None and end is None:
            return (
                text,
                {"total_lines": total_lines, "returned_lines": total_lines, "truncated": False},
            )
        sliced = lines[(start - 1) if start else 0 : end if end else total_lines]
        return (
            "\n".join(sliced),
            {"total_lines": total_lines, "returned_lines": len(sliced), "truncated": False},
        )

    def list_dir(self, path: str):
        target = self.root / path if path else self.root
        return [p.name for p in target.iterdir()]

    def is_dir(self, path: str):
        return (self.root / path).is_dir()

    def iter_files(self, base: str = ""):
        base_path = self.root / base if base else self.root
        for path in base_path.rglob("*"):
            if path.is_file():
                yield str(path.relative_to(self.root))


def _seed_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "hello.py").write_text("def hello():\n    return 'world'\n")
    (workspace / "goodbye.py").write_text("def goodbye():\n    return 'farewell'\n")
    return workspace


def _decode(result) -> dict:
    return json.loads(result.content[0].text)


def _populate_index(workspace: Path, store: ExploreStore) -> None:
    reindex(store, workspace, options=ReindexOptions(timeout_ms=5000))


def _attach_session(store: ExploreStore, workspace: Path) -> _FakeSession:
    """Return a session with the full ``ExploreIndex`` handle (matches prod).

    The store and the session share the same on-disk index directory so
    rows written by ``_populate_index`` are visible to the handler.
    """
    handle = ExploreIndex(
        workspace_root=workspace,
        index_root=store.db_path.parent,
        store=store,
        generation=1,
    )
    return _FakeSession(handle)


# --- canonical reason-code coverage ----------------------------------------


def test_canonical_reason_codes_cover_all_f1_f20() -> None:
    """Every reason code in the fault matrix is in the canonical set."""
    expected = {
        "no_committed_generation",
        "version_mismatch",
        "index_corrupt",
        "interrupted_build",
        "index_locked",
        "index_unwritable",
        "index_read_only",
        "index_stale_scope",
        "ignore_rule_changed",
        "hard_file_skipped",
        "pattern_not_fts_eligible",
        "timeout_exceeded",
        "indexer_error",
        "resource_pressure",
        "workspace_moved",
        "no_index_handle",
    }
    assert expected == CANONICAL_REASON_CODES


def test_unknown_reason_code_raises_at_boundary() -> None:
    """Typo / drift in the reason code: helper rejects it fail-closed."""
    with pytest.raises(ValueError, match="unknown fallback_reason"):
        serving_metadata(object(), index_used=False, fallback_reason="bogus_reason")


def test_serving_metadata_block_shape() -> None:
    """The canonical block carries exactly the documented fields."""
    block = serving_metadata(object(), index_used=True, fallback_reason=None)
    assert block["index_used"] is True
    assert block["fallback_reason"] is None
    assert "index_staleness" in block
    staleness = block["index_staleness"]
    assert isinstance(staleness, dict)
    assert set(staleness.keys()) >= {
        "stale_paths_count",
        "last_refresh_age",
        "recovery_willfallback",
    }


# --- per-tool coverage -----------------------------------------------------


def test_grep_files_carries_serving_metadata(tmp_path: Path) -> None:
    workspace = _seed_workspace(tmp_path)
    store = ExploreStore(tmp_path / ".agent" / "ralph-explore")
    try:
        _populate_index(workspace, store)
        result = handle_grep_files(
            _attach_session(store, workspace),
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
        assert "index_used" in payload
        assert "fallback_reason" in payload
        assert "index_staleness" in payload
        assert payload["index_used"] is True
        assert payload["fallback_reason"] is None
    finally:
        store.close()


def test_grep_files_use_index_always_failure_reports_reason(tmp_path: Path) -> None:
    """``use_index='always'`` against an ineligible pattern returns a reason."""
    workspace = _seed_workspace(tmp_path)
    store = ExploreStore(tmp_path / ".agent" / "ralph-explore")
    try:
        _populate_index(workspace, store)
        from ralph.mcp.tools.coordination import InvalidParamsError

        with pytest.raises(InvalidParamsError):
            handle_grep_files(
                _attach_session(store, workspace),
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


def test_read_file_carries_serving_metadata(tmp_path: Path) -> None:
    workspace = _seed_workspace(tmp_path)
    store = ExploreStore(tmp_path / ".agent" / "ralph-explore")
    try:
        _populate_index(workspace, store)
        result = handle_read_file(
            _attach_session(store, workspace),
            _Workspace(workspace),
            {"path": "hello.py", "return_metadata": True},
        )
        payload = _decode(result)
        assert "index_used" in payload
        assert "index_staleness" in payload
        assert payload["index_used"] is True
    finally:
        store.close()


def test_read_multiple_files_carries_serving_metadata(tmp_path: Path) -> None:
    workspace = _seed_workspace(tmp_path)
    store = ExploreStore(tmp_path / ".agent" / "ralph-explore")
    try:
        _populate_index(workspace, store)
        result = handle_read_multiple_files(
            _attach_session(store, workspace),
            _Workspace(workspace),
            {"paths": ["hello.py", "goodbye.py"]},
        )
        payload = _decode(result)
        assert "index_used" in payload
        assert "index_staleness" in payload
    finally:
        store.close()


def test_list_directory_carries_serving_metadata(tmp_path: Path) -> None:
    workspace = _seed_workspace(tmp_path)
    store = ExploreStore(tmp_path / ".agent" / "ralph-explore")
    try:
        _populate_index(workspace, store)
        result = handle_list_directory(
            _attach_session(store, workspace),
            _Workspace(workspace),
            {"path": ".", "view": "compact", "include_counts": True},
        )
        payload = _decode(result)
        assert "index_used" in payload
        assert "index_staleness" in payload
    finally:
        store.close()


def test_directory_tree_carries_serving_metadata(tmp_path: Path) -> None:
    workspace = _seed_workspace(tmp_path)
    store = ExploreStore(tmp_path / ".agent" / "ralph-explore")
    try:
        _populate_index(workspace, store)
        result = handle_directory_tree(
            _attach_session(store, workspace),
            _Workspace(workspace),
            {"path": ".", "view": "compact", "include_counts": True},
        )
        payload = _decode(result)
        assert "tree" in payload
        assert "index_used" in payload
        assert "index_staleness" in payload
    finally:
        store.close()


def test_search_files_carries_serving_metadata(tmp_path: Path) -> None:
    workspace = _seed_workspace(tmp_path)
    store = ExploreStore(tmp_path / ".agent" / "ralph-explore")
    try:
        _populate_index(workspace, store)
        result = handle_search_files(
            _attach_session(store, workspace),
            _Workspace(workspace),
            {"pattern": "**/*.py", "path": ".", "role": "source"},
        )
        payload = _decode(result)
        assert "index_used" in payload
        assert "index_staleness" in payload
    finally:
        store.close()


def test_ralph_graph_carries_serving_metadata(tmp_path: Path) -> None:
    workspace = _seed_workspace(tmp_path)
    store = ExploreStore(tmp_path / ".agent" / "ralph-explore")
    try:
        _populate_index(workspace, store)
        result = handle_ralph_graph(
            _attach_session(store, workspace),
            _Workspace(workspace),
            {"query_type": "hubs", "scope_path": ".", "limit": 5},
        )
        payload = _decode(result)
        assert "index_used" in payload
        assert "index_staleness" in payload
    finally:
        store.close()


def test_ralph_index_status_carries_serving_metadata(tmp_path: Path) -> None:
    workspace = _seed_workspace(tmp_path)
    store = ExploreStore(tmp_path / ".agent" / "ralph-explore")
    try:
        _populate_index(workspace, store)
        result = handle_ralph_index_status(
            _attach_session(store, workspace),
            _Workspace(workspace),
            {},
        )
        payload = _decode(result)
        assert "index_used" in payload
        assert "index_staleness" in payload
    finally:
        store.close()


def test_ralph_reindex_carries_serving_metadata(tmp_path: Path) -> None:
    workspace = _seed_workspace(tmp_path)
    store = ExploreStore(tmp_path / ".agent" / "ralph-explore")
    try:
        result = handle_ralph_reindex(
            _attach_session(store, workspace),
            _Workspace(workspace),
            {"mode": "full", "timeout_ms": 5000},
        )
        payload = _decode(result)
        assert "index_used" in payload
        assert "index_staleness" in payload
    finally:
        store.close()


def test_serving_metadata_caches_staleness_block_until_state_changes(
    tmp_path: Path,
) -> None:
    """The staleness block cache avoids repeat SQL while state is stable."""
    workspace = _seed_workspace(tmp_path)
    store = ExploreStore(tmp_path / ".agent" / "ralph-explore")
    try:
        _populate_index(workspace, store)
        session = _attach_session(store, workspace)
        # First call populates the cache.
        serving_metadata(session, index_used=True, fallback_reason=None)
        cache_slot = session.explore_index.staleness_block_cache
        assert cache_slot is not None
        first_signature, first_block = cache_slot
        first_stale_count = first_block["stale_paths_count"]
        # Second call with no store mutation must reuse the cache slot.
        serving_metadata(session, index_used=True, fallback_reason=None)
        second_slot = session.explore_index.staleness_block_cache
        assert second_slot is not None
        assert second_slot[0] == first_signature
        # The integer staleness count is identical (only the wall-clock
        # ``last_refresh_age`` may advance between calls).
        assert second_slot[1]["stale_paths_count"] == first_stale_count
        assert second_slot[1]["recovery_willfallback"] == first_block["recovery_willfallback"]
        # Mutate the store and confirm the next call recomputes.
        session.explore_index.store.mark_dirty("hello.py", reason="test_cache", source_tool="test")
        serving_metadata(session, index_used=True, fallback_reason=None)
        third_slot = session.explore_index.staleness_block_cache
        assert third_slot is not None
        assert third_slot[0] != first_signature
        assert third_slot[1]["stale_paths_count"] == first_stale_count + 1
    finally:
        store.close()
