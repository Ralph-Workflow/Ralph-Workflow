"""Tests for ralph/workspace/fs.py — Filesystem workspace implementation."""

from __future__ import annotations

import tempfile
from pathlib import Path

from ralph.workspace.fs import FsWorkspace


class TestFsWorkspaceIterFiles:
    def test_iter_files_returns_files_under_base(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = FsWorkspace(tmpdir)
            (Path(tmpdir) / "file1.txt").write_text("", encoding="utf-8")
            (Path(tmpdir) / "file2.txt").write_text("", encoding="utf-8")

            files = ws.iter_files(".")

            assert "file1.txt" in files
            assert "file2.txt" in files

    def test_iter_files_excludes_skipped_directories(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = FsWorkspace(tmpdir)
            (Path(tmpdir) / "file.txt").write_text("", encoding="utf-8")
            (Path(tmpdir) / "subdir").mkdir(parents=True)
            (Path(tmpdir) / "subdir" / "nested.txt").write_text("", encoding="utf-8")
            (Path(tmpdir) / ".git").mkdir(parents=True)
            (Path(tmpdir) / ".git" / "config").write_text("", encoding="utf-8")

            files = ws.iter_files(".")

            assert "file.txt" in files
            assert "subdir/nested.txt" in files
            assert ".git/config" not in files

    def test_iter_files_nonexistent_base_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = FsWorkspace(tmpdir)

            files = ws.iter_files("nonexistent")

            assert files == ()
