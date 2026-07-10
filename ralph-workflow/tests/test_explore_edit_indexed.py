"""Black-box tests for edit_file indexed safety args.

AC-10: edit_file accepts prompt-exact ``target`` /
``match_strategy`` ``exact|within_target|all_in_target`` /
``expected_content_hash`` / ``impact_preview`` /
``reindex`` ``auto|skip|changed_blocking`` /
``return_evidence_updates`` while preserving edit-area checks,
dry-run diffs, and dirty marking.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import TYPE_CHECKING, cast
from unittest.mock import MagicMock

from ralph.mcp.explore.dirty_paths import build_sqlite_index_handle
from ralph.mcp.explore.pipeline import ReindexOptions, reindex
from ralph.mcp.explore.store import ExploreStore
from ralph.mcp.tools.bridge._specs_file_write import file_write_specs
from ralph.mcp.tools.workspace._write_handlers import handle_edit_file

if TYPE_CHECKING:
    from ralph.mcp.tools.bridge._tool_spec import ToolSpec
    from ralph.mcp.tools.coordination import ToolContent


class _FakeSession:
    def __init__(self, explore_index=None) -> None:
        self.explore_index = explore_index

    def check_capability(self, capability: str) -> dict[str, str]:
        return {"status": "approved", "capability": capability}

    def check_edit_area(self, path: str) -> dict[str, str]:
        return {"status": "approved", "path": path}


def _edit_spec() -> ToolSpec:
    for spec in file_write_specs():
        if spec.metadata.definition.name == "edit_file":
            return spec
    raise AssertionError("edit_file not in file_write_specs")


def _seed_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "module.py").write_text(
        "def hello():\n    return 1\n\nclass Foo:\n    def bar(self):\n        return 2\n"
    )
    return workspace


def test_edit_file_accepts_only_prompt_exact_match_strategy_values() -> None:
    """AC-10 schema: ``match_strategy`` enum must be
    ``exact|within_target|all_in_target``.
    """
    spec = _edit_spec()
    properties = spec.metadata.definition.input_schema.get("properties", {})
    enum_values = properties["match_strategy"]["enum"]
    assert enum_values == ["exact", "within_target", "all_in_target"]


def test_edit_file_schema_exposes_expected_content_hash_and_target() -> None:
    """AC-10 schema: target, expected_content_hash, impact_preview,
    reindex, return_evidence_updates are all part of the public schema.
    """
    spec = _edit_spec()
    properties = spec.metadata.definition.input_schema.get("properties", {})
    for arg in (
        "expected_content_hash",
        "target",
        "match_strategy",
        "reindex",
        "impact_preview",
        "return_evidence_updates",
    ):
        assert arg in properties, f"{arg} missing from edit_file schema"
    assert properties["reindex"]["enum"] == ["auto", "skip", "changed_blocking"]


def test_edit_file_rejects_invalid_match_strategy(tmp_path: Path) -> None:
    """AC-10: edit_file fails closed on unsupported match_strategy."""
    workspace = _seed_workspace(tmp_path)
    from ralph.mcp.tools.coordination import InvalidParamsError

    session = _FakeSession()
    ws = MagicMock()
    ws.read.return_value = "def hello():\n    return 1\n"
    ws.write.return_value = None
    ws.is_path_git_tracked.return_value = False
    ws.absolute_path.return_value = str(workspace / "module.py")
    try:
        handle_edit_file(
            session,
            ws,
            {
                "path": "module.py",
                "edits": [{"oldText": "return 1", "newText": "return 2"}],
                "match_strategy": "fuzzy",
            },
        )
    except InvalidParamsError:
        return
    raise AssertionError("Expected InvalidParamsError")


def test_edit_file_rejects_expected_content_hash_mismatch_before_mutation(
    tmp_path: Path,
) -> None:
    """AC-10: hash mismatch fails closed before any mutation."""
    workspace = _seed_workspace(tmp_path)
    session = _FakeSession()
    ws = MagicMock()
    original = "def hello():\n    return 1\n"
    ws.read.return_value = original
    ws.write.return_value = None
    ws.is_path_git_tracked.return_value = False
    ws.absolute_path.return_value = str(workspace / "module.py")
    bogus_hash = hashlib.sha256(b"different content").hexdigest()
    result = handle_edit_file(
        session,
        ws,
        {
            "path": "module.py",
            "edits": [{"oldText": "return 1", "newText": "return 2"}],
            "expected_content_hash": bogus_hash,
        },
    )
    assert result.is_error is True
    payload = json.loads(cast("ToolContent", result.content[0]).text)
    assert payload["status"] == "stale_evidence"
    assert payload["expected_content_hash"] == bogus_hash
    assert payload["reason"] == "content_changed"
    ws.write.assert_not_called()


def test_edit_file_target_symbol_returns_impact_preview_and_evidence_updates(
    tmp_path: Path,
) -> None:
    """AC-10: target symbol dry_run with impact_preview returns the
    payload; return_evidence_updates includes generation/freshness.
    """
    workspace = _seed_workspace(tmp_path)
    store = ExploreStore(tmp_path / ".agent" / "ralph-explore")
    try:
        reindex(store, workspace, options=ReindexOptions(timeout_ms=5000))
        session = _FakeSession(explore_index=build_sqlite_index_handle(store))
        # Locate the indexed ``hello`` symbol row.
        hello_symbols = [
            sym for sym in store.iter_symbols() if sym.name == "hello"
        ]
        assert len(hello_symbols) == 1
        sym = hello_symbols[0]
        ws = MagicMock()
        ws.read.return_value = "def hello():\n    return 1\n"
        ws.write.return_value = None
        ws.is_path_git_tracked.return_value = False
        ws.absolute_path.return_value = str(workspace / "module.py")
        preview_result = handle_edit_file(
            session,
            ws,
            {
                "path": "module.py",
                "edits": [{"oldText": "return 1", "newText": "return 2"}],
                "target": {"symbol": sym.qualified_name, "path": "module.py"},
                "match_strategy": "within_target",
                "dry_run": True,
                "impact_preview": True,
            },
        )
        preview_payload = json.loads(
            cast("ToolContent", preview_result.content[0]).text
        )
        assert preview_payload["status"] == "preview"
        assert "impact_preview" in preview_payload
        assert preview_payload["impact_preview"]["available"] is True

        apply_result = handle_edit_file(
            session,
            ws,
            {
                "path": "module.py",
                "edits": [{"oldText": "return 1", "newText": "return 2"}],
                "target": {"symbol": sym.qualified_name, "path": "module.py"},
                "match_strategy": "within_target",
                "return_evidence_updates": True,
            },
        )
        apply_payload = json.loads(
            cast("ToolContent", apply_result.content[0]).text
        )
        assert apply_payload["status"] == "applied"
        assert "evidence_updates" in apply_payload
        assert apply_payload["evidence_updates"]["dirty_path"] == "module.py"
        assert apply_payload["evidence_updates"]["is_stale"] is True
    finally:
        store.close()


def test_edit_file_legacy_oldtext_newtext_contract_still_works(tmp_path: Path) -> None:
    """The legacy ``oldText/newText`` contract still works without an index."""
    workspace = _seed_workspace(tmp_path)
    session = _FakeSession()
    ws = MagicMock()
    ws.read.return_value = "def hello():\n    return 1\n"
    ws.write.return_value = None
    ws.is_path_git_tracked.return_value = False
    ws.absolute_path.return_value = str(workspace / "module.py")
    result = handle_edit_file(
        session,
        ws,
        {
            "path": "module.py",
            "edits": [{"oldText": "return 1", "newText": "return 2"}],
        },
    )
    payload = json.loads(cast("ToolContent", result.content[0]).text)
    assert payload["status"] == "applied"
    assert "diff" in payload
    assert "bytes_written" in payload


def test_edit_file_ambiguous_symbol_target_returns_structured_error(
    tmp_path: Path,
) -> None:
    """AC-10: ambiguous / unresolved symbol targets fail closed before
    any mutation.
    """
    workspace = _seed_workspace(tmp_path)
    store = ExploreStore(tmp_path / ".agent" / "ralph-explore")
    try:
        reindex(store, workspace, options=ReindexOptions(timeout_ms=5000))
        session = _FakeSession(explore_index=build_sqlite_index_handle(store))
        ws = MagicMock()
        ws.read.return_value = "def hello():\n    return 1\n"
        ws.write.return_value = None
        ws.is_path_git_tracked.return_value = False
        ws.absolute_path.return_value = str(workspace / "module.py")
        result = handle_edit_file(
            session,
            ws,
            {
                "path": "module.py",
                "edits": [{"oldText": "return 1", "newText": "return 2"}],
                "target": {"symbol": "nonexistent_symbol"},
            },
        )
        assert result.is_error is True
        payload = json.loads(cast("ToolContent", result.content[0]).text)
        assert payload["status"] == "ambiguous_target"
        assert payload["reason"] == "target_unresolved"
        ws.write.assert_not_called()
    finally:
        store.close()


def test_edit_file_cross_file_evidence_target_is_rejected(tmp_path: Path) -> None:
    """AC-10: an evidence target that points to another file must be rejected."""
    workspace = _seed_workspace(tmp_path)
    store = ExploreStore(tmp_path / ".agent" / "ralph-explore")
    try:
        reindex(store, workspace, options=ReindexOptions(timeout_ms=5000))
        # Build an evidence row that points at a sibling file.
        from ralph.mcp.explore.store import (
            EvidenceRow,
            derive_evidence_id,
        )

        ev_id = derive_evidence_id(
            path="other_module.py",
            content_hash="any",
            start_line=1,
            end_line=2,
            kind="path",
            extractor_version="phase1-lexical-v1",
        )
        store.insert_evidence(
            EvidenceRow(
                evidence_id=ev_id,
                path="other_module.py",
                start_line=1,
                end_line=2,
                content_hash="any",
                generation=1,
                source_tool="test",
                evidence_kind="path",
                created_at=0.0,
                is_stale=False,
            )
        )
        session = _FakeSession(explore_index=build_sqlite_index_handle(store))
        ws = MagicMock()
        ws.read.return_value = "def hello():\n    return 1\n"
        ws.write.return_value = None
        ws.is_path_git_tracked.return_value = False
        ws.absolute_path.return_value = str(workspace / "module.py")
        result = handle_edit_file(
            session,
            ws,
            {
                "path": "module.py",
                "edits": [{"oldText": "return 1", "newText": "return 2"}],
                "target": {"evidence_id": ev_id},
            },
        )
        assert result.is_error is True
        payload = json.loads(cast("ToolContent", result.content[0]).text)
        assert payload["status"] == "ambiguous_target"
        assert payload["reason"] == "target_path_mismatch"
        assert payload["target_path"] == "other_module.py"
        assert payload["edit_path"] == "module.py"
        ws.write.assert_not_called()
    finally:
        store.close()


def test_edit_file_reindex_skip_still_marks_dirty(tmp_path: Path) -> None:
    """AC-04 + AC-10: ``reindex='skip'`` still marks the path dirty."""
    workspace = _seed_workspace(tmp_path)
    store = ExploreStore(tmp_path / ".agent" / "ralph-explore")
    try:
        session = _FakeSession(explore_index=build_sqlite_index_handle(store))
        ws = MagicMock()
        ws.read.return_value = "def hello():\n    return 1\n"
        ws.write.return_value = None
        ws.is_path_git_tracked.return_value = False
        ws.absolute_path.return_value = str(workspace / "module.py")
        handle_edit_file(
            session,
            ws,
            {
                "path": "module.py",
                "edits": [{"oldText": "return 1", "newText": "return 2"}],
                "reindex": "skip",
            },
        )
        assert store.peek_dirty_paths() == ["module.py"]
    finally:
        store.close()
