"""Tests for ralph/workspace/fs.py — Filesystem workspace implementation."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.workspace.fs import FsWorkspace

if TYPE_CHECKING:
    from pathlib import Path


class TestFsWorkspaceCopy:
    def test_copy_file(self, tmp_path: Path) -> None:
        ws = FsWorkspace(tmp_path)
        (tmp_path / "src.txt").write_text("content", encoding="utf-8")

        ws.copy("src.txt", "dest.txt")

        assert (tmp_path / "src.txt").exists()
        assert (tmp_path / "dest.txt").read_text(encoding="utf-8") == "content"

    def test_copy_file_with_overwrite(self, tmp_path: Path) -> None:
        ws = FsWorkspace(tmp_path)
        (tmp_path / "src.txt").write_text("src", encoding="utf-8")
        (tmp_path / "dest.txt").write_text("dest", encoding="utf-8")

        ws.copy("src.txt", "dest.txt", overwrite=True)

        assert (tmp_path / "dest.txt").read_text(encoding="utf-8") == "src"

    def test_copy_directory(self, tmp_path: Path) -> None:
        ws = FsWorkspace(tmp_path)
        subdir = tmp_path / "src_dir"
        subdir.mkdir()
        (subdir / "file.txt").write_text("nested", encoding="utf-8")

        ws.copy("src_dir", "dest_dir")

        assert (tmp_path / "dest_dir" / "file.txt").read_text(encoding="utf-8") == "nested"
