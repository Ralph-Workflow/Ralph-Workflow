"""Tests for artifact history archival and indexing."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.mcp.artifacts.history import (
    history_dir_for_artifact,
    history_index_path,
    rebuild_history_index,
)

if TYPE_CHECKING:
    from pathlib import Path


def _now_iso() -> str:
    return "2026-05-06T12:00:00+00:00"


class TestRebuildHistoryIndex:
    def test_no_op_when_history_dir_missing(self, tmp_path: Path) -> None:
        artifact_dir = tmp_path / ".agent" / "artifacts"
        rebuild_history_index(artifact_dir, "plan")
        # no error and no index created
        assert not history_index_path(artifact_dir, "plan").exists()

    def test_deletes_index_when_no_json_files(self, tmp_path: Path) -> None:
        artifact_dir = tmp_path / ".agent" / "artifacts"
        hist_dir = history_dir_for_artifact(artifact_dir, "plan")
        hist_dir.mkdir(parents=True)
        index = hist_dir / "index.md"
        index.write_text("stale", encoding="utf-8")

        rebuild_history_index(artifact_dir, "plan")

        assert not index.exists()

    def test_generates_index_listing_archived_entries(self, tmp_path: Path) -> None:
        artifact_dir = tmp_path / ".agent" / "artifacts"
        hist_dir = history_dir_for_artifact(artifact_dir, "plan")
        hist_dir.mkdir(parents=True)
        (hist_dir / "20260506T120000_plan.json").write_text("{}", encoding="utf-8")

        rebuild_history_index(artifact_dir, "plan")

        index = hist_dir / "index.md"
        assert index.exists()
        content = index.read_text(encoding="utf-8")
        assert "20260506T120000" in content
        assert "20260506T120000_plan.json" in content

    def test_index_includes_markdown_when_present(self, tmp_path: Path) -> None:
        artifact_dir = tmp_path / ".agent" / "artifacts"
        hist_dir = history_dir_for_artifact(artifact_dir, "plan")
        hist_dir.mkdir(parents=True)
        (hist_dir / "20260506T120000_plan.json").write_text("{}", encoding="utf-8")
        (hist_dir / "20260506T120000_plan.md").write_text("# Plan", encoding="utf-8")

        rebuild_history_index(artifact_dir, "plan")

        content = (hist_dir / "index.md").read_text(encoding="utf-8")
        assert "20260506T120000_plan.md" in content
