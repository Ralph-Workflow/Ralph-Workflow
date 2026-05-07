"""Tests for artifact history archival and indexing."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.mcp.artifacts.history import (
    archive_artifact_before_overwrite,
    clear_artifact_history,
    history_dir_for_artifact,
    history_index_path,
    rebuild_history_index,
)

if TYPE_CHECKING:
    from pathlib import Path


def _now_iso() -> str:
    return "2026-05-06T12:00:00+00:00"


class TestHistoryDirPaths:
    def test_history_dir_under_artifact_dir(self, tmp_path: Path) -> None:
        artifact_dir = tmp_path / ".agent" / "artifacts"
        result = history_dir_for_artifact(artifact_dir, "plan")
        assert result == artifact_dir / "history" / "plan"

    def test_history_index_path_inside_history_dir(self, tmp_path: Path) -> None:
        artifact_dir = tmp_path / ".agent" / "artifacts"
        result = history_index_path(artifact_dir, "plan")
        assert result == artifact_dir / "history" / "plan" / "index.md"


class TestArchiveArtifactBeforeOverwrite:
    def test_returns_empty_when_no_canonical_artifact(self, tmp_path: Path) -> None:
        artifact_dir = tmp_path / ".agent" / "artifacts"
        artifact_dir.mkdir(parents=True)

        created = archive_artifact_before_overwrite(
            artifact_dir, tmp_path, "plan", now_iso=_now_iso
        )

        assert created == []

    def test_archives_json_when_canonical_exists(self, tmp_path: Path) -> None:
        artifact_dir = tmp_path / ".agent" / "artifacts"
        artifact_dir.mkdir(parents=True)
        (artifact_dir / "plan.json").write_text('{"type":"plan"}', encoding="utf-8")

        created = archive_artifact_before_overwrite(
            artifact_dir, tmp_path, "plan", now_iso=_now_iso
        )

        assert len(created) >= 1
        hist_dir = history_dir_for_artifact(artifact_dir, "plan")
        assert any(p.suffix == ".json" for p in created)
        assert all(p.parent == hist_dir for p in created)

    def test_archives_markdown_handoff_when_present(self, tmp_path: Path) -> None:
        artifact_dir = tmp_path / ".agent" / "artifacts"
        artifact_dir.mkdir(parents=True)
        (artifact_dir / "plan.json").write_text('{"type":"plan"}', encoding="utf-8")
        # Write a handoff file at the known handoff path for 'plan'
        from ralph.mcp.artifacts.handoffs import handoff_path_for_artifact  # noqa: PLC0415

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
        (artifact_dir / "plan.json").write_text('{"type":"plan"}', encoding="utf-8")

        archive_artifact_before_overwrite(
            artifact_dir, tmp_path, "plan", now_iso=_now_iso
        )

        index = history_index_path(artifact_dir, "plan")
        assert index.exists()
        content = index.read_text(encoding="utf-8")
        assert "plan" in content.lower()

    def test_timestamped_filename_uses_iso_format(self, tmp_path: Path) -> None:
        artifact_dir = tmp_path / ".agent" / "artifacts"
        artifact_dir.mkdir(parents=True)
        (artifact_dir / "plan.json").write_text('{"type":"plan"}', encoding="utf-8")

        created = archive_artifact_before_overwrite(
            artifact_dir, tmp_path, "plan", now_iso=_now_iso
        )

        json_archives = [p for p in created if p.suffix == ".json"]
        assert len(json_archives) == 1
        assert json_archives[0].name.startswith("20260506T120000_plan")


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


class TestClearArtifactHistory:
    def test_no_op_when_history_dir_missing(self, tmp_path: Path) -> None:
        artifact_dir = tmp_path / ".agent" / "artifacts"
        clear_artifact_history(artifact_dir, "plan")  # must not raise

    def test_removes_archived_json_and_md_files(self, tmp_path: Path) -> None:
        artifact_dir = tmp_path / ".agent" / "artifacts"
        hist_dir = history_dir_for_artifact(artifact_dir, "plan")
        hist_dir.mkdir(parents=True)
        json_file = hist_dir / "20260506T120000_plan.json"
        md_file = hist_dir / "20260506T120000_plan.md"
        index_file = hist_dir / "index.md"
        json_file.write_text("{}", encoding="utf-8")
        md_file.write_text("# Plan", encoding="utf-8")
        index_file.write_text("# Index", encoding="utf-8")

        clear_artifact_history(artifact_dir, "plan")

        assert not json_file.exists()
        assert not md_file.exists()
        assert not index_file.exists()

    def test_history_dir_itself_remains_after_clear(self, tmp_path: Path) -> None:
        artifact_dir = tmp_path / ".agent" / "artifacts"
        hist_dir = history_dir_for_artifact(artifact_dir, "plan")
        hist_dir.mkdir(parents=True)
        (hist_dir / "20260506T120000_plan.json").write_text("{}", encoding="utf-8")

        clear_artifact_history(artifact_dir, "plan")

        assert hist_dir.exists()
