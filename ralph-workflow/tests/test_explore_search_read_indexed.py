"""Black-box tests for indexed ``read_file``, ``read_multiple_files``,
and ``search_files`` behavior."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from ralph.mcp.explore.dirty_paths import build_sqlite_index_handle
from ralph.mcp.explore.pipeline import ReindexOptions, reindex
from ralph.mcp.explore.store import ExploreStore, derive_evidence_id
from ralph.mcp.tools.workspace._read_handlers import (
    handle_read_file,
    handle_read_multiple_files,
    handle_search_files,
)


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

    def read_lines(
        self,
        path: str,
        *,
        start=None,
        end=None,
        head=None,
        tail=None,
    ):
        text = self.read(path)
        lines = text.splitlines(keepends=False)
        total_lines = len(lines)
        if head is not None:
            sliced = lines[:head]
            return ("\n".join(sliced), {"total_lines": total_lines, "returned_lines": len(sliced), "truncated": False})
        if tail is not None:
            sliced = lines[-tail:]
            return ("\n".join(sliced), {"total_lines": total_lines, "returned_lines": len(sliced), "truncated": False})
        if start is None and end is None:
            return (text, {"total_lines": total_lines, "returned_lines": total_lines, "truncated": False})
        sliced = lines[(start - 1) if start else 0 : end if end else total_lines]
        return ("\n".join(sliced), {"total_lines": total_lines, "returned_lines": len(sliced), "truncated": False})

    def read_bytes(self, path: str, *, offset=None, limit=None):
        data = self.read(path).encode("utf-8")
        if offset is not None:
            data = data[offset:]
        if limit is not None:
            data = data[:limit]
        return (data.decode("utf-8"), {"total_bytes": len(data), "returned_bytes": len(data), "truncated": False})

    def list_dir(self, base: str):
        target = self.root / base if base else self.root
        return [p.name for p in target.iterdir()]

    def iter_files(self, base: str):
        base_path = self.root / base if base else self.root
        for path in base_path.rglob("*"):
            if path.is_file():
                yield str(path.relative_to(self.root))


def _seed_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "a.py").write_text(
        "def hello():\n    return 'world'\n\ndef goodbye():\n    return 'farewell'\n"
    )
    return workspace


def _decode(result) -> dict:
    return json.loads(result.content[0].text)


def _hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def test_read_file_evidence_id_returns_span(tmp_path: Path) -> None:
    workspace = _seed_workspace(tmp_path)
    store = ExploreStore(tmp_path / ".agent" / "ralph-explore")
    try:
        reindex(store, workspace, options=ReindexOptions(timeout_ms=5000))
        # Pick a known evidence id (chunk spans 1-5 for the seed content).
        evidence_id = derive_evidence_id(
            path="a.py",
            content_hash=_hash((workspace / "a.py").read_text()),
            start_line=1,
            end_line=5,
            kind="chunk",
            extractor_version="phase1-lexical-v1",
        )
        session = _FakeSession(build_sqlite_index_handle(store))
        result = handle_read_file(
            session,
            _Workspace(workspace),
            {"evidence_id": evidence_id, "context_lines": 1},
        )
        assert result.is_error is False
        payload = _decode(result)
        assert payload["evidence_id"] == evidence_id
        assert payload["start_line"] == 1
        assert payload["end_line"] == 5
        assert "hello" in payload["content"]
    finally:
        store.close()


def test_read_file_evidence_id_fails_closed_on_stale_hash(tmp_path: Path) -> None:
    workspace = _seed_workspace(tmp_path)
    store = ExploreStore(tmp_path / ".agent" / "ralph-explore")
    try:
        reindex(store, workspace, options=ReindexOptions(timeout_ms=5000))
        # Re-index will compute the right content_hash; we want to
        # pass a WRONG expected_content_hash and observe fail-closed.
        evidence_id = derive_evidence_id(
            path="a.py",
            content_hash=_hash((workspace / "a.py").read_text()),
            start_line=1,
            end_line=5,
            kind="chunk",
            extractor_version="phase1-lexical-v1",
        )
        session = _FakeSession(build_sqlite_index_handle(store))
        result = handle_read_file(
            session,
            _Workspace(workspace),
            {"evidence_id": evidence_id, "expected_content_hash": "deadbeef"},
        )
        payload = _decode(result)
        assert result.is_error is True
        assert payload["status"] == "stale_evidence"
    finally:
        store.close()


def test_read_file_evidence_id_unknown(tmp_path: Path) -> None:
    workspace = _seed_workspace(tmp_path)
    store = ExploreStore(tmp_path / ".agent" / "ralph-explore")
    try:
        reindex(store, workspace, options=ReindexOptions(timeout_ms=5000))
        session = _FakeSession(build_sqlite_index_handle(store))
        from ralph.mcp.tools.coordination import ToolError

        with pytest.raises(ToolError):
            handle_read_file(
                session, _Workspace(workspace), {"evidence_id": "missing-ev"}
            )
    finally:
        store.close()


def test_read_file_span_id_unknown_returns_unknown_evidence(tmp_path: Path) -> None:
    workspace = _seed_workspace(tmp_path)
    store = ExploreStore(tmp_path / ".agent" / "ralph-explore")
    try:
        reindex(store, workspace, options=ReindexOptions(timeout_ms=5000))
        session = _FakeSession(build_sqlite_index_handle(store))
        result = handle_read_file(
            session,
            _Workspace(workspace),
            {"span_id": "span-missing-1"},
        )
        payload = _decode(result)
        assert result.is_error is True
        assert payload["status"] == "unknown_evidence"
        assert payload["span_id"] == "span-missing-1"
    finally:
        store.close()


def test_read_file_symbol_unknown_returns_unknown_evidence(tmp_path: Path) -> None:
    workspace = _seed_workspace(tmp_path)
    store = ExploreStore(tmp_path / ".agent" / "ralph-explore")
    try:
        reindex(store, workspace, options=ReindexOptions(timeout_ms=5000))
        session = _FakeSession(build_sqlite_index_handle(store))
        result = handle_read_file(
            session,
            _Workspace(workspace),
            {"symbol": "no_such_symbol_xyz"},
        )
        payload = _decode(result)
        assert result.is_error is True
        assert payload["status"] == "unknown_evidence"
        assert payload["symbol"] == "no_such_symbol_xyz"
    finally:
        store.close()


def test_read_file_span_id_without_index_returns_unavailable(tmp_path: Path) -> None:
    workspace = _seed_workspace(tmp_path)
    session = _FakeSession(explore_index=None)
    result = handle_read_file(
        session,
        _Workspace(workspace),
        {"span_id": "span-1"},
    )
    payload = _decode(result)
    assert result.is_error is True
    assert payload["status"] == "indexed_selector_unavailable"
    assert payload["reason"] == "no_explore_index_handle"


def test_read_file_symbol_without_index_returns_unavailable(tmp_path: Path) -> None:
    workspace = _seed_workspace(tmp_path)
    session = _FakeSession(explore_index=None)
    result = handle_read_file(
        session,
        _Workspace(workspace),
        {"symbol": "hello"},
    )
    payload = _decode(result)
    assert result.is_error is True
    assert payload["status"] == "indexed_selector_unavailable"
    assert payload["reason"] == "no_explore_index_handle"


def test_read_file_returns_legacy_shape_without_index(tmp_path: Path) -> None:
    workspace = _seed_workspace(tmp_path)
    session = _FakeSession(explore_index=None)
    result = handle_read_file(session, _Workspace(workspace), {"path": "a.py"})
    assert result.is_error is False
    assert "hello" in result.content[0].text


def test_read_file_with_expected_content_hash_succeeds(tmp_path: Path) -> None:
    workspace = _seed_workspace(tmp_path)
    actual_hash = _hash((workspace / "a.py").read_text())
    session = _FakeSession(explore_index=None)
    result = handle_read_file(
        session,
        _Workspace(workspace),
        {"path": "a.py", "expected_content_hash": actual_hash},
    )
    assert result.is_error is False
    assert "hello" in result.content[0].text


def test_read_file_with_expected_content_hash_fails(tmp_path: Path) -> None:
    workspace = _seed_workspace(tmp_path)
    session = _FakeSession(explore_index=None)
    result = handle_read_file(
        session,
        _Workspace(workspace),
        {"path": "a.py", "expected_content_hash": "deadbeef"},
    )
    payload = _decode(result)
    assert result.is_error is True
    assert payload["status"] == "stale_evidence"


def test_read_multiple_files_legacy_paths(tmp_path: Path) -> None:
    workspace = _seed_workspace(tmp_path)
    session = _FakeSession(explore_index=None)
    result = handle_read_multiple_files(
        session,
        _Workspace(workspace),
        {"paths": ["a.py"]},
    )
    payload = _decode(result)
    assert payload["files"][0]["content"]
    assert result.is_error is False


def test_read_multiple_files_mixed_items(tmp_path: Path) -> None:
    workspace = _seed_workspace(tmp_path)
    store = ExploreStore(tmp_path / ".agent" / "ralph-explore")
    try:
        reindex(store, workspace, options=ReindexOptions(timeout_ms=5000))
        evidence_id = derive_evidence_id(
            path="a.py",
            content_hash=_hash((workspace / "a.py").read_text()),
            start_line=1,
            end_line=5,
            kind="chunk",
            extractor_version="phase1-lexical-v1",
        )
        session = _FakeSession(build_sqlite_index_handle(store))
        result = handle_read_multiple_files(
            session,
            _Workspace(workspace),
            {
                "items": [
                    {"path": "a.py", "line_start": 1, "line_end": 5},
                    {"evidence_id": evidence_id},
                    {"span_id": "span-missing-x"},
                    {"symbol": "no_such_symbol_zzz"},
                ],
                "fail_fast": False,
            },
        )
        payload = _decode(result)
        files = payload["files"]
        assert len(files) == 4
        # First item is path-based.
        assert files[0].get("content") is not None
        # Second is evidence-based.
        assert files[1].get("evidence_id") == evidence_id
        # Third + fourth resolve via the indexed store and return
        # structured unknown_evidence for the missing span / symbol.
        assert files[2].get("status") == "unknown_evidence"
        assert files[2].get("selector") == "span_id"
        assert files[3].get("status") == "unknown_evidence"
        assert files[3].get("selector") == "symbol"
    finally:
        store.close()


def test_search_files_ranked_returns_score_reasons(tmp_path: Path) -> None:
    workspace = _seed_workspace(tmp_path)
    session = _FakeSession(explore_index=None)
    result = handle_search_files(
        session,
        _Workspace(workspace),
        {"pattern": "**/*.py", "path": ".", "ranked": True},
    )
    payload = _decode(result)
    assert "score_reasons" in payload
    for entry in payload["score_reasons"]:
        assert "score" in entry
        assert "score_reasons" in entry


def test_search_files_role_filter(tmp_path: Path) -> None:
    workspace = _seed_workspace(tmp_path)
    (workspace / "tests").mkdir()
    (workspace / "tests" / "test_a.py").write_text("def test_x(): pass")
    session = _FakeSession(explore_index=None)
    # role=source keeps .py files.
    result_source = handle_search_files(
        session,
        _Workspace(workspace),
        {"pattern": "**/*.py", "path": ".", "role": "source"},
    )
    payload_source = _decode(result_source)
    assert any("a.py" in m for m in payload_source["matches"])
    # role=test keeps test_* only.
    result_test = handle_search_files(
        session,
        _Workspace(workspace),
        {"pattern": "**/*.py", "path": ".", "role": "test"},
    )
    payload_test = _decode(result_test)
    assert all("test_" in m for m in payload_test["matches"])


def test_search_files_changed_only_returns_empty_in_phase1(tmp_path: Path) -> None:
    workspace = _seed_workspace(tmp_path)
    session = _FakeSession(explore_index=None)
    result = handle_search_files(
        session,
        _Workspace(workspace),
        {"pattern": "**/*.py", "path": ".", "changed_only": True},
    )
    payload = _decode(result)
    assert payload["matches"] == []


def test_search_files_contains_symbol_without_index_reports_fallback(tmp_path: Path) -> None:
    workspace = _seed_workspace(tmp_path)
    session = _FakeSession(explore_index=None)
    result = handle_search_files(
        session,
        _Workspace(workspace),
        {"pattern": "**/*.py", "path": ".", "contains_symbol": "hello"},
    )
    payload = _decode(result)
    assert payload["contains_symbol_note"] == "no_explore_index_handle"
