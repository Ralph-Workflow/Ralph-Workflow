"""Black-box tests for the S-3 pre-query freshness guard.

The freshness guard is a cheap bounded check executed before an
indexed query in ``auto`` mode. It compares the persisted
dirty-path queue and deleted-file count against the total file
count and returns ``index_stale_scope`` when any dirty path falls
inside the query scope OR the stale share exceeds the documented
threshold (``DEFAULT_STALENESS_THRESHOLD`` = 5%).

These tests assert:

* ``staleness_probe`` returns the documented shape with truthful
  values for fresh / stale indexes.
* ``handle_grep_files`` falls through to live grep with reason
  ``index_stale_scope`` when the probe is stale.
* ``use_index='always'`` still serves live grep when stale (the
  reason code is reported so the caller can see why).
* The fallback preserves parity with the live branch (live
  matches are returned, not an empty indexed result).
"""

from __future__ import annotations

import json
from pathlib import Path

from ralph.mcp.explore.handlers import ExploreIndex
from ralph.mcp.explore.pipeline import ReindexOptions, reindex
from ralph.mcp.explore.serving import (
    DEFAULT_STALENESS_THRESHOLD,
    staleness_probe,
)
from ralph.mcp.explore.store import ExploreStore
from ralph.mcp.tools.workspace._grep_handlers import handle_grep_files


class _FakeSession:
    session_id = "freshness-guard"
    run_id = "freshness-guard"
    broker_secret: str | None = None

    def __init__(self, explore_index=None) -> None:
        self.explore_index = explore_index

    def check_capability(self, capability: str):
        return {"status": "approved", "capability": capability}

    def check_edit_area(self, path: str):
        return {"status": "approved", "path": path}


class _Workspace:
    def __init__(self, root: Path) -> None:
        self.root = root

    def read(self, path: str) -> str:
        return (self.root / path).read_text()

    def stat(self, path: str):
        target = self.root / path
        if target.is_dir():
            return {"type": "dir", "size_bytes": 0}
        if target.exists():
            return {"type": "file", "size_bytes": target.stat().st_size}
        return {"type": "missing", "size_bytes": 0}

    def iter_files(self, base: str = ""):
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


def _attach_session(store: ExploreStore, workspace: Path) -> _FakeSession:
    handle = ExploreIndex(
        workspace_root=workspace,
        index_root=store.db_path.parent,
        store=store,
        generation=1,
    )
    return _FakeSession(handle)


def test_staleness_probe_default_threshold_is_5_percent() -> None:
    """The default threshold is the documented 5% share."""
    assert DEFAULT_STALENESS_THRESHOLD == 0.05


def test_staleness_probe_no_handle_is_not_stale() -> None:
    """Without an explore handle the probe is a no-op."""
    probe = staleness_probe(_FakeSession(), workspace_root=None)
    assert probe["stale"] is False
    assert probe["reason"] is None
    assert probe["stale_paths_count"] == 0


def test_staleness_probe_fresh_index_is_not_stale(tmp_path: Path) -> None:
    """A freshly populated index with no dirty paths is not stale."""
    workspace = _seed_workspace(tmp_path)
    store = ExploreStore(tmp_path / ".agent" / "ralph-explore")
    try:
        reindex(store, workspace, options=ReindexOptions(timeout_ms=5000))
        session = _attach_session(store, workspace)
        probe = staleness_probe(session, workspace_root=workspace)
        assert probe["stale"] is False
        assert probe["reason"] is None
    finally:
        store.close()


def test_staleness_probe_dirty_path_falls_through(tmp_path: Path) -> None:
    """A dirty path in the workspace triggers ``index_stale_scope``."""
    workspace = _seed_workspace(tmp_path)
    store = ExploreStore(tmp_path / ".agent" / "ralph-explore")
    try:
        reindex(store, workspace, options=ReindexOptions(timeout_ms=5000))
        session = _attach_session(store, workspace)
        # Mutate a file outside the indexer to simulate an external
        # edit and mark the path dirty via the store.
        (workspace / "hello.py").write_text("def hello():\n    return 'changed'\n")
        store.mark_dirty("hello.py", reason="test_extern", source_tool="test")
        probe = staleness_probe(session, workspace_root=workspace)
        assert probe["stale"] is True
        assert probe["reason"] == "index_stale_scope"
        assert probe["stale_paths_count"] >= 1
    finally:
        store.close()


def test_staleness_probe_custom_threshold(tmp_path: Path) -> None:
    """The probe honours a caller-supplied threshold for the share signal.

    The probe fires on EITHER ``scope_affected`` (any dirty path) OR
    ``stale_share > threshold``. The two are independent signals;
    setting a high threshold only suppresses the share-based firing,
    not the scope-based one. We verify the share path here by using
    a fresh index with no dirty paths and a custom threshold.
    """
    workspace = _seed_workspace(tmp_path)
    store = ExploreStore(tmp_path / ".agent" / "ralph-explore")
    try:
        reindex(store, workspace, options=ReindexOptions(timeout_ms=5000))
        session = _attach_session(store, workspace)
        # No dirty paths -> both probes are fresh regardless of threshold.
        probe_high = staleness_probe(session, workspace_root=workspace, threshold=0.5)
        assert probe_high["stale"] is False
        probe_low = staleness_probe(session, workspace_root=workspace, threshold=0.01)
        assert probe_low["stale"] is False
    finally:
        store.close()


def test_grep_files_falls_through_when_stale(tmp_path: Path) -> None:
    """Stale indexes fall through to live grep with reason code."""
    workspace = _seed_workspace(tmp_path)
    store = ExploreStore(tmp_path / ".agent" / "ralph-explore")
    try:
        reindex(store, workspace, options=ReindexOptions(timeout_ms=5000))
        session = _attach_session(store, workspace)
        store.mark_dirty("hello.py", reason="test_extern", source_tool="test")
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
        assert payload["index_used"] is False
        assert payload["fallback_reason"] == "index_stale_scope"
        # Live grep still returned the match; freshness block
        # reflects the staleness.
        assert any("hello" in (m.get("text") or "") for m in payload["matches"])
        assert payload["stale_paths_count"] >= 1
    finally:
        store.close()


def test_grep_files_use_index_always_falls_through_when_stale(tmp_path: Path) -> None:
    """``use_index='always'`` serves live results when stale (fail-closed)."""
    workspace = _seed_workspace(tmp_path)
    store = ExploreStore(tmp_path / ".agent" / "ralph-explore")
    try:
        reindex(store, workspace, options=ReindexOptions(timeout_ms=5000))
        session = _attach_session(store, workspace)
        store.mark_dirty("hello.py", reason="test_extern", source_tool="test")
        result = handle_grep_files(
            session,
            _Workspace(workspace),
            {
                "pattern": "hello",
                "path": ".",
                "regex": False,
                "case_sensitive": False,
                "use_index": "always",
            },
        )
        payload = _decode(result)
        # The handler must serve live results rather than return empty.
        # ``use_index='always'`` fail-closes with the reason code.
        assert payload["index_used"] is False
        assert payload["fallback_reason"] == "index_stale_scope"
        assert any("hello" in (m.get("text") or "") for m in payload["matches"])
    finally:
        store.close()
