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
    assert DEFAULT_STALENESS_THRESHOLD == 0.05


def test_staleness_probe_no_handle_is_not_stale() -> None:
    probe = staleness_probe(_FakeSession(), workspace_root=None)
    assert probe["stale"] is False
    assert probe["reason"] is None
    assert probe["stale_paths_count"] == 0


def test_staleness_probe_fresh_index_is_not_stale(tmp_path: Path) -> None:
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
    workspace = _seed_workspace(tmp_path)
    store = ExploreStore(tmp_path / ".agent" / "ralph-explore")
    try:
        reindex(store, workspace, options=ReindexOptions(timeout_ms=5000))
        session = _attach_session(store, workspace)
        (workspace / "hello.py").write_text("def hello():\n    return 'changed'\n")
        store.mark_dirty("hello.py", reason="test_extern", source_tool="test")
        probe = staleness_probe(session, workspace_root=workspace)
        assert probe["stale"] is True
        assert probe["reason"] == "index_stale_scope"
        assert probe["stale_paths_count"] >= 1
    finally:
        store.close()


def test_staleness_probe_custom_threshold(tmp_path: Path) -> None:
    workspace = _seed_workspace(tmp_path)
    store = ExploreStore(tmp_path / ".agent" / "ralph-explore")
    try:
        reindex(store, workspace, options=ReindexOptions(timeout_ms=5000))
        session = _attach_session(store, workspace)
        probe_high = staleness_probe(session, workspace_root=workspace, threshold=0.5)
        assert probe_high["stale"] is False
        probe_low = staleness_probe(session, workspace_root=workspace, threshold=0.01)
        assert probe_low["stale"] is False
    finally:
        store.close()


def test_grep_files_falls_through_when_stale(tmp_path: Path) -> None:
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
        assert any("hello" in (m.get("text") or "") for m in payload["matches"])
        assert payload["stale_paths_count"] >= 1
    finally:
        store.close()


def test_grep_files_use_index_always_returns_structured_error_when_stale(tmp_path: Path) -> None:
    import pytest

    from ralph.mcp.tools.coordination import InvalidParamsError

    workspace = _seed_workspace(tmp_path)
    store = ExploreStore(tmp_path / ".agent" / "ralph-explore")
    try:
        reindex(store, workspace, options=ReindexOptions(timeout_ms=5000))
        session = _attach_session(store, workspace)
        store.mark_dirty("hello.py", reason="test_extern", source_tool="test")
        with pytest.raises(InvalidParamsError) as excinfo:
            handle_grep_files(
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
        assert "index_stale_scope" in str(excinfo.value)
        assert "reason_code" in str(excinfo.value)
    finally:
        store.close()
