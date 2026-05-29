"""Tests for ralph/workspace/fs.py — Filesystem workspace implementation."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from ralph.workspace.fs import FsWorkspace

if TYPE_CHECKING:
    from pathlib import Path


class TestFsWorkspaceDelete:
    def test_delete_file(self, tmp_path: Path) -> None:
        ws = FsWorkspace(tmp_path)
        (tmp_path / "todelete.txt").write_text("content", encoding="utf-8")

        ws.delete("todelete.txt")

        assert (tmp_path / "todelete.txt").exists() is False

    def test_delete_directory_without_recursive_raises(self, tmp_path: Path) -> None:
        ws = FsWorkspace(tmp_path)
        (tmp_path / "subdir").mkdir()

        with pytest.raises(IsADirectoryError):
            ws.delete("subdir")

    def test_delete_directory_recursive(self, tmp_path: Path) -> None:
        ws = FsWorkspace(tmp_path)
        subdir = tmp_path / "subdir"
        subdir.mkdir()
        (subdir / "file.txt").write_text("content", encoding="utf-8")

        ws.delete("subdir", recursive=True)

        assert (tmp_path / "subdir").exists() is False
