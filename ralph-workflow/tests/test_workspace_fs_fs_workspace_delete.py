"""Tests for ralph/workspace/fs.py — Filesystem workspace implementation."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from ralph.workspace.fs import FsWorkspace


class TestFsWorkspaceDelete:
    def test_delete_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = FsWorkspace(tmpdir)
            (Path(tmpdir) / "todelete.txt").write_text("content", encoding="utf-8")

            ws.delete("todelete.txt")

            assert (Path(tmpdir) / "todelete.txt").exists() is False

    def test_delete_directory_without_recursive_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = FsWorkspace(tmpdir)
            (Path(tmpdir) / "subdir").mkdir()

            with pytest.raises(IsADirectoryError):
                ws.delete("subdir")

    def test_delete_directory_recursive(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = FsWorkspace(tmpdir)
            subdir = Path(tmpdir) / "subdir"
            subdir.mkdir()
            (subdir / "file.txt").write_text("content", encoding="utf-8")

            ws.delete("subdir", recursive=True)

            assert (Path(tmpdir) / "subdir").exists() is False
