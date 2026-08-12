"""Reindex-pipeline invariant tests (S-3c).

Exercises the real ``ralph.mcp.explore.pipeline.reindex`` for the
product acceptance criteria: no-change no-op (AC-03), localized-change
(AC-04), ranked search with evidence (AC-05), indexed read fallback
(AC-06), and delete/rebuild equivalence (AC-08).
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.mcp.explore.pipeline import ReindexOptions, reindex
from ralph.mcp.explore.store import ExploreStore
from ralph.mcp.tools.workspace._read_handlers import handle_read_file, handle_search_files

if TYPE_CHECKING:
    from ralph.mcp.tools.tool_result import ToolResult


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

    def write(self, path: str, content: str) -> None:
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)

    def read(self, path: str) -> str:
        return (self.root / path).read_text()

    def stat(self, path: str) -> dict[str, object]:
        target = self.root / path
        if target.is_dir():
            return {"type": "dir", "size_bytes": 0}
        if target.exists():
            return {"type": "file", "size_bytes": target.stat().st_size}
        return {"type": "missing", "size_bytes": 0}

    def read_lines(self, path: str, **_kwargs: object) -> tuple[str, dict[str, object]]:
        text = self.read(path)
        lines = text.splitlines(keepends=False)
        return text, {
            "total_lines": len(lines),
            "returned_lines": len(lines),
            "truncated": False,
        }

    def read_bytes(
        self, path: str, *, offset: int | None = None, limit: int | None = None
    ) -> tuple[str, dict[str, object]]:
        data = self.read(path).encode("utf-8")
        if offset is not None:
            data = data[offset:]
        if limit is not None:
            data = data[:limit]
        return data.decode("utf-8"), {
            "total_bytes": len(data),
            "returned_bytes": len(data),
            "truncated": False,
        }

    def list_dir(self, base: str) -> list[str]:
        target = self.root / base if base else self.root
        return [p.name for p in target.iterdir()]

    def iter_files(self, base: str) -> object:
        base_path = self.root / base if base else self.root
        return (
            str(path.relative_to(self.root))
            for path in base_path.rglob("*")
            if path.is_file()
        )


def _seed_five_files(tmp_path: Path) -> Path:
    """Seed a workspace with five Python files."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    for name in ("a.py", "b.py", "c.py", "d.py", "e.py"):
        (workspace / name).write_text(f"def fn_{name[:-3]}():\n    return 1\n")
    return workspace


def _decode(result: ToolResult) -> dict[str, object]:
    return json.loads(result.content[0].text)


def _hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def test_cold_build_parses_all_files(tmp_path: Path) -> None:
    """AC-05 foundation: cold build parses all five files."""
    workspace = _seed_five_files(tmp_path)
    store = ExploreStore(tmp_path / ".agent" / "ralph-explore")
    try:
        result = reindex(store, workspace, options=ReindexOptions(timeout_ms=5000))
        assert result.status == "ok"
        assert result.parse_count >= 5
    finally:
        store.close()


def test_warm_no_op_reindex_parses_zero(tmp_path: Path) -> None:
    """AC-03: a no-change refresh reprocesses no project content.

    A warm no-op returns ``skipped_no_changes`` (the coalescing signal)
    with ``parse_count == 0``; the AC-03 invariant is the zero parse
    count, not the status token.
    """
    workspace = _seed_five_files(tmp_path)
    store = ExploreStore(tmp_path / ".agent" / "ralph-explore")
    try:
        reindex(store, workspace, options=ReindexOptions(timeout_ms=5000))
        second = reindex(store, workspace, options=ReindexOptions(timeout_ms=5000))
        assert second.status in {"ok", "skipped_no_changes"}
        assert second.parse_count == 0
    finally:
        store.close()


def test_localized_change_parses_one(tmp_path: Path) -> None:
    """AC-04: a localized change refreshes only the affected file."""
    workspace = _seed_five_files(tmp_path)
    store = ExploreStore(tmp_path / ".agent" / "ralph-explore")
    try:
        reindex(store, workspace, options=ReindexOptions(timeout_ms=5000))
        # Mutate exactly one file's bytes.
        (workspace / "c.py").write_text("def fn_c():\n    return 22\n")
        third = reindex(store, workspace, options=ReindexOptions(timeout_ms=5000))
        assert third.status == "ok"
        assert third.parse_count == 1
    finally:
        store.close()


def test_search_ranking_carries_score_reasons(tmp_path: Path) -> None:
    """AC-05: ranked search results carry score_reasons."""
    workspace = _seed_five_files(tmp_path)
    session = _FakeSession(explore_index=None)
    result = handle_search_files(
        session,
        _Workspace(workspace),
        {"pattern": "**/*.py", "path": ".", "ranked": True},
    )
    payload = _decode(result)
    assert "score_reasons" in payload
    reasons_list = payload["score_reasons"]
    assert isinstance(reasons_list, list)
    assert len(reasons_list) >= 1
    for entry in reasons_list:
        assert isinstance(entry, dict)
        assert "score" in entry
        assert "score_reasons" in entry


def test_delete_and_rebuild_yields_equivalent_results(tmp_path: Path) -> None:
    """AC-08: deleting derived knowledge and rebuilding produces equivalent results."""
    workspace = _seed_five_files(tmp_path)
    store_path = tmp_path / ".agent" / "ralph-explore"
    store = ExploreStore(store_path)
    try:
        first = reindex(store, workspace, options=ReindexOptions(timeout_ms=5000))
        assert first.status == "ok"
        first_count = first.parse_count
    finally:
        store.close()
    # Delete the entire derived intelligence directory.
    shutil.rmtree(store_path)
    store = ExploreStore(store_path)
    try:
        rebuilt = reindex(store, workspace, options=ReindexOptions(timeout_ms=5000))
        assert rebuilt.status == "ok"
        assert rebuilt.parse_count >= 5
        assert rebuilt.parse_count == first_count
    finally:
        store.close()


def test_read_file_legacy_fallback_without_index(tmp_path: Path) -> None:
    """AC-06: read_file without an index returns the legacy live shape."""
    workspace = _seed_five_files(tmp_path)
    session = _FakeSession(explore_index=None)
    result = handle_read_file(session, _Workspace(workspace), {"path": "a.py"})
    assert result.is_error is False
    text = result.content[0].text
    assert "fn_a" in text


def test_read_file_expected_hash_succeeds(tmp_path: Path) -> None:
    """AC-06: read_file with a matching expected_content_hash succeeds."""
    workspace = _seed_five_files(tmp_path)
    actual_hash = _hash((workspace / "a.py").read_text())
    session = _FakeSession(explore_index=None)
    result = handle_read_file(
        session,
        _Workspace(workspace),
        {"path": "a.py", "expected_content_hash": actual_hash},
    )
    assert result.is_error is False


def test_read_file_stale_hash_returns_stale_evidence(tmp_path: Path) -> None:
    """AC-06: read_file with a stale expected_content_hash returns stale_evidence."""
    workspace = _seed_five_files(tmp_path)
    session = _FakeSession(explore_index=None)
    result = handle_read_file(
        session,
        _Workspace(workspace),
        {"path": "a.py", "expected_content_hash": "deadbeef"},
    )
    payload = _decode(result)
    assert result.is_error is True
    assert payload["status"] == "stale_evidence"
