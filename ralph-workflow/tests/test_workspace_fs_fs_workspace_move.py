"""Tests for ralph/workspace/fs.py — Filesystem workspace implementation."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from ralph.workspace.fs import FsWorkspace

if TYPE_CHECKING:
    from pathlib import Path


class TestFsWorkspaceMove:
    def test_move_file(self, tmp_path: Path) -> None:
        ws = FsWorkspace(tmp_path)
        (tmp_path / "src.txt").write_text("content", encoding="utf-8")

        ws.move("src.txt", "dest.txt")

        assert (tmp_path / "src.txt").exists() is False
        assert (tmp_path / "dest.txt").read_text(encoding="utf-8") == "content"

    def test_move_with_overwrite(self, tmp_path: Path) -> None:
        ws = FsWorkspace(tmp_path)
        (tmp_path / "src.txt").write_text("src", encoding="utf-8")
        (tmp_path / "dest.txt").write_text("dest", encoding="utf-8")

        ws.move("src.txt", "dest.txt", overwrite=True)

        assert (tmp_path / "dest.txt").read_text(encoding="utf-8") == "src"

    def test_move_without_overwrite_raises(self, tmp_path: Path) -> None:
        ws = FsWorkspace(tmp_path)
        (tmp_path / "src.txt").write_text("src", encoding="utf-8")
        (tmp_path / "dest.txt").write_text("dest", encoding="utf-8")

        with pytest.raises(FileExistsError):
            ws.move("src.txt", "dest.txt")
