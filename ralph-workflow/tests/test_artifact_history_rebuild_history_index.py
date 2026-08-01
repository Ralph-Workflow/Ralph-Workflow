"""Tests for artifact history archival and indexing."""

from __future__ import annotations

from pathlib import Path

from ralph.mcp.artifacts.file_backend import FileBackend
from ralph.mcp.artifacts.history import (
    history_dir_for_artifact,
    history_index_path,
    rebuild_history_index,
)


def _now_iso() -> str:
    return "2026-05-06T12:00:00+00:00"


class _RecordingHistoryBackend(FileBackend):
    """In-memory history storage that exposes observable physical writes."""

    def __init__(self, files: dict[Path, str]) -> None:
        self.files = files
        self.write_count = 0

    def exists(self, path: Path) -> bool:
        return path in self.files or any(candidate.parent == path for candidate in self.files)

    def mkdir(self, path: Path, *, parents: bool = False, exist_ok: bool = False) -> None:
        del path, parents, exist_ok

    def read_text(self, path: Path, *, encoding: str = "utf-8") -> str:
        del encoding
        return self.files[path]

    def write_text(self, path: Path, content: str, *, encoding: str = "utf-8") -> None:
        del encoding
        self.write_count += 1
        self.files[path] = content

    def replace(self, source: Path, destination: Path) -> None:
        self.files[destination] = self.files.pop(source)

    def sync_directory(self, path: Path) -> None:
        del path

    def unlink(self, path: Path, *, missing_ok: bool = False) -> None:
        if missing_ok:
            self.files.pop(path, None)
            return
        del self.files[path]

    def glob(self, path: Path, pattern: str) -> list[Path]:
        del pattern
        return [candidate for candidate in self.files if candidate.parent == path]


class TestRebuildHistoryIndex:
    def test_no_op_when_history_dir_missing(self, tmp_path: Path) -> None:
        artifact_dir = tmp_path / ".agent" / "artifacts"
        rebuild_history_index(artifact_dir, "plan")
        # no error and no index created
        assert not history_index_path(artifact_dir, "plan").exists()

    def test_deletes_index_when_no_markdown_files(self, tmp_path: Path) -> None:
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
        (hist_dir / "20260506T120000_plan.md").write_text("# Plan", encoding="utf-8")

        rebuild_history_index(artifact_dir, "plan")

        index = hist_dir / "index.md"
        assert index.exists()
        content = index.read_text(encoding="utf-8")
        assert "20260506T120000" in content
        assert "20260506T120000_plan.md" in content

    def test_rebuild_regression_skips_identical_index_rewrite(self) -> None:
        """S-3: rebuilding unchanged history leaves the index mutation-free."""
        artifact_dir = Path("/virtual/.agent/artifacts")
        history_dir = history_dir_for_artifact(artifact_dir, "plan")
        backend = _RecordingHistoryBackend(
            {history_dir / "20260506T120000_plan.md": "# Plan"}
        )

        rebuild_history_index(artifact_dir, "plan", backend=backend)
        rebuild_history_index(artifact_dir, "plan", backend=backend)

        assert backend.write_count == 1
        assert "20260506T120000_plan.md" in backend.files[history_index_path(artifact_dir, "plan")]

    def test_index_includes_markdown_when_present(self, tmp_path: Path) -> None:
        artifact_dir = tmp_path / ".agent" / "artifacts"
        hist_dir = history_dir_for_artifact(artifact_dir, "plan")
        hist_dir.mkdir(parents=True)
        (hist_dir / "20260506T120000_plan.md").write_text("# Plan", encoding="utf-8")
        (hist_dir / "20260506T120000_1_plan.md").write_text("# Plan handoff", encoding="utf-8")

        rebuild_history_index(artifact_dir, "plan")

        content = (hist_dir / "index.md").read_text(encoding="utf-8")
        assert "20260506T120000_plan.md" in content
