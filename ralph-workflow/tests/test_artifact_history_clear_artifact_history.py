"""Tests for artifact history archival and indexing."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.mcp.artifacts.history import (
    clear_artifact_history,
    history_dir_for_artifact,
)

if TYPE_CHECKING:
    from pathlib import Path


def _now_iso() -> str:
    return "2026-05-06T12:00:00+00:00"


class TestClearArtifactHistory:
    def test_no_op_when_history_dir_missing(self, tmp_path: Path) -> None:
        artifact_dir = tmp_path / ".agent" / "artifacts"
        clear_artifact_history(artifact_dir, "plan")  # must not raise

    def test_removes_archived_markdown_files_and_index(self, tmp_path: Path) -> None:
        artifact_dir = tmp_path / ".agent" / "artifacts"
        hist_dir = history_dir_for_artifact(artifact_dir, "plan")
        hist_dir.mkdir(parents=True)
        md_file = hist_dir / "20260506T120000_plan.md"
        second_md_file = hist_dir / "20260506T120000_1_plan.md"
        index_file = hist_dir / "index.md"
        md_file.write_text("# Plan", encoding="utf-8")
        second_md_file.write_text("# Plan handoff", encoding="utf-8")
        index_file.write_text("# Index", encoding="utf-8")

        clear_artifact_history(artifact_dir, "plan")

        assert not md_file.exists()
        assert not second_md_file.exists()
        assert not index_file.exists()

    def test_history_dir_itself_remains_after_clear(self, tmp_path: Path) -> None:
        artifact_dir = tmp_path / ".agent" / "artifacts"
        hist_dir = history_dir_for_artifact(artifact_dir, "plan")
        hist_dir.mkdir(parents=True)
        (hist_dir / "20260506T120000_plan.md").write_text("# Plan", encoding="utf-8")

        clear_artifact_history(artifact_dir, "plan")

        assert hist_dir.exists()
