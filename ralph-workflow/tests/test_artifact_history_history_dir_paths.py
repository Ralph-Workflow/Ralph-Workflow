"""Tests for artifact history archival and indexing."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.mcp.artifacts.history import (
    history_dir_for_artifact,
    history_index_path,
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
