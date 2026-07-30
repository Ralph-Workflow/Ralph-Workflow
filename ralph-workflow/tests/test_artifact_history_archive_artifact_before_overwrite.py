"""Tests for artifact history archival and indexing."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.mcp.artifacts.handoffs import handoff_path_for_artifact
from ralph.mcp.artifacts.history import (
    archive_artifact_before_overwrite,
    history_dir_for_artifact,
    history_index_path,
)

if TYPE_CHECKING:
    from pathlib import Path


def _now_iso() -> str:
    return "2026-05-06T12:00:00+00:00"


class TestArchiveArtifactBeforeOverwrite:
    def test_returns_empty_when_no_canonical_artifact(self, tmp_path: Path) -> None:
        artifact_dir = tmp_path / ".agent" / "artifacts"
        artifact_dir.mkdir(parents=True)

        created = archive_artifact_before_overwrite(
            artifact_dir, tmp_path, "plan", now_iso=_now_iso
        )

        assert created == []

    def test_archives_markdown_when_canonical_exists(self, tmp_path: Path) -> None:
        artifact_dir = tmp_path / ".agent" / "artifacts"
        artifact_dir.mkdir(parents=True)
        (artifact_dir / "plan.md").write_text("# Plan", encoding="utf-8")

        created = archive_artifact_before_overwrite(
            artifact_dir, tmp_path, "plan", now_iso=_now_iso
        )

        assert len(created) >= 1
        hist_dir = history_dir_for_artifact(artifact_dir, "plan")
        assert any(p.suffix == ".md" for p in created)
        assert all(p.parent == hist_dir for p in created)

    def test_archives_markdown_handoff_when_present(self, tmp_path: Path) -> None:
        artifact_dir = tmp_path / ".agent" / "artifacts"
        artifact_dir.mkdir(parents=True)
        (artifact_dir / "plan.md").write_text("# Plan", encoding="utf-8")
        # Write a handoff file at the known handoff path for 'plan'

        handoff_rel = handoff_path_for_artifact("plan")
        if handoff_rel:
            handoff_abs = tmp_path / handoff_rel
            handoff_abs.parent.mkdir(parents=True, exist_ok=True)
            handoff_abs.write_text("# Plan", encoding="utf-8")

        created = archive_artifact_before_overwrite(
            artifact_dir, tmp_path, "plan", now_iso=_now_iso
        )

        if handoff_rel:
            assert any(p.suffix == ".md" for p in created)

    def test_builds_index_after_archive(self, tmp_path: Path) -> None:
        artifact_dir = tmp_path / ".agent" / "artifacts"
        artifact_dir.mkdir(parents=True)
        (artifact_dir / "plan.md").write_text("# Plan", encoding="utf-8")

        archive_artifact_before_overwrite(artifact_dir, tmp_path, "plan", now_iso=_now_iso)

        index = history_index_path(artifact_dir, "plan")
        assert index.exists()
        content = index.read_text(encoding="utf-8")
        assert "plan" in content.lower()

    def test_timestamped_filename_uses_iso_format(self, tmp_path: Path) -> None:
        artifact_dir = tmp_path / ".agent" / "artifacts"
        artifact_dir.mkdir(parents=True)
        (artifact_dir / "plan.md").write_text("# Plan", encoding="utf-8")

        created = archive_artifact_before_overwrite(
            artifact_dir, tmp_path, "plan", now_iso=_now_iso
        )

        markdown_archives = [p for p in created if p.suffix == ".md"]
        assert len(markdown_archives) == 1
        assert markdown_archives[0].name.startswith("20260506T120000_plan")
