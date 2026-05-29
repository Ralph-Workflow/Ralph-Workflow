"""Tests for ralph/workspace/fs.py — Filesystem workspace implementation."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.workspace.fs import FsWorkspace

if TYPE_CHECKING:
    from pathlib import Path


class TestFsWorkspaceListDir:
    def test_lists_directory_contents(self, tmp_path: Path) -> None:
        ws = FsWorkspace(tmp_path)
        (tmp_path / "a.txt").write_text("", encoding="utf-8")
        (tmp_path / "b.txt").write_text("", encoding="utf-8")

        entries = ws.list_dir(".")

        assert "a.txt" in entries
        assert "b.txt" in entries

    def test_list_dir_empty_for_nonexistent(self, tmp_path: Path) -> None:
        ws = FsWorkspace(tmp_path)

        entries = ws.list_dir("nonexistent")

        assert entries == []
