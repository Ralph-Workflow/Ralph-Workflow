"""Tests for prompt materialization review/fix handoff payloads."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.prompts.materialize import _resolve_fix_result_content
from ralph.workspace.fs import FsWorkspace

if TYPE_CHECKING:
    from pathlib import Path


def test_resolve_fix_result_content_reads_fix_result_artifact(tmp_path: Path) -> None:
    workspace = FsWorkspace(tmp_path)
    artifact_dir = tmp_path / ".agent" / "artifacts"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    expected = '{"summary": "Applied fixes"}'
    (artifact_dir / "fix_result.json").write_text(expected, encoding="utf-8")

    content, path = _resolve_fix_result_content(workspace)
    assert "# Fix Result" in content
    assert "Applied fixes" in content
    assert path == str(tmp_path / ".agent" / "FIX_RESULT.md")


def test_resolve_fix_result_content_returns_placeholder_when_missing(tmp_path: Path) -> None:
    workspace = FsWorkspace(tmp_path)

    content, path = _resolve_fix_result_content(workspace)
    assert content == "(no fix result available)"
    assert path == ""
