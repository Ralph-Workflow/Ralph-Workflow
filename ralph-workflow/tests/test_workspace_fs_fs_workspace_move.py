"""Tests for ralph/workspace/fs.py — Filesystem workspace implementation."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from ralph.workspace.fs import FsWorkspace


class TestFsWorkspaceMove:
    def test_move_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = FsWorkspace(tmpdir)
            (Path(tmpdir) / "src.txt").write_text("content", encoding="utf-8")

            ws.move("src.txt", "dest.txt")

            assert (Path(tmpdir) / "src.txt").exists() is False
            assert (Path(tmpdir) / "dest.txt").read_text(encoding="utf-8") == "content"

    def test_move_with_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = FsWorkspace(tmpdir)
            (Path(tmpdir) / "src.txt").write_text("src", encoding="utf-8")
            (Path(tmpdir) / "dest.txt").write_text("dest", encoding="utf-8")

            ws.move("src.txt", "dest.txt", overwrite=True)

            assert (Path(tmpdir) / "dest.txt").read_text(encoding="utf-8") == "src"

    def test_move_without_overwrite_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = FsWorkspace(tmpdir)
            (Path(tmpdir) / "src.txt").write_text("src", encoding="utf-8")
            (Path(tmpdir) / "dest.txt").write_text("dest", encoding="utf-8")

            with pytest.raises(FileExistsError):
                ws.move("src.txt", "dest.txt")
