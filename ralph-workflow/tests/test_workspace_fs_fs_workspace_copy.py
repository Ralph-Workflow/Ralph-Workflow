"""Tests for ralph/workspace/fs.py — Filesystem workspace implementation."""

from __future__ import annotations

import tempfile
from pathlib import Path

from ralph.workspace.fs import FsWorkspace


class TestFsWorkspaceCopy:
    def test_copy_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = FsWorkspace(tmpdir)
            (Path(tmpdir) / "src.txt").write_text("content", encoding="utf-8")

            ws.copy("src.txt", "dest.txt")

            assert (Path(tmpdir) / "src.txt").exists()
            assert (Path(tmpdir) / "dest.txt").read_text(encoding="utf-8") == "content"

    def test_copy_file_with_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = FsWorkspace(tmpdir)
            (Path(tmpdir) / "src.txt").write_text("src", encoding="utf-8")
            (Path(tmpdir) / "dest.txt").write_text("dest", encoding="utf-8")

            ws.copy("src.txt", "dest.txt", overwrite=True)

            assert (Path(tmpdir) / "dest.txt").read_text(encoding="utf-8") == "src"

    def test_copy_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = FsWorkspace(tmpdir)
            subdir = Path(tmpdir) / "src_dir"
            subdir.mkdir()
            (subdir / "file.txt").write_text("nested", encoding="utf-8")

            ws.copy("src_dir", "dest_dir")

            assert (Path(tmpdir) / "dest_dir" / "file.txt").read_text(encoding="utf-8") == "nested"
