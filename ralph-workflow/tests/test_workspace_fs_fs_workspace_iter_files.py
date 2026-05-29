"""Tests for ralph/workspace/fs.py — Filesystem workspace implementation."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.workspace.fs import FsWorkspace

if TYPE_CHECKING:
    from pathlib import Path


class TestFsWorkspaceIterFiles:
    def test_iter_files_returns_files_under_base(self, tmp_path: Path) -> None:
        ws = FsWorkspace(tmp_path)
        (tmp_path / "file1.txt").write_text("", encoding="utf-8")
        (tmp_path / "file2.txt").write_text("", encoding="utf-8")

        files = ws.iter_files(".")

        assert "file1.txt" in files
        assert "file2.txt" in files

    def test_iter_files_excludes_skipped_directories(self, tmp_path: Path) -> None:
        ws = FsWorkspace(tmp_path)
        (tmp_path / "file.txt").write_text("", encoding="utf-8")
        (tmp_path / "subdir").mkdir(parents=True)
        (tmp_path / "subdir" / "nested.txt").write_text("", encoding="utf-8")
        (tmp_path / ".git").mkdir(parents=True)
        (tmp_path / ".git" / "config").write_text("", encoding="utf-8")

        files = ws.iter_files(".")

        assert "file.txt" in files
        assert "subdir/nested.txt" in files
        assert ".git/config" not in files

    def test_iter_files_nonexistent_base_returns_empty(self, tmp_path: Path) -> None:
        ws = FsWorkspace(tmp_path)

        files = ws.iter_files("nonexistent")

        assert files == ()
