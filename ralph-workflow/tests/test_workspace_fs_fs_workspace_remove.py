"""Tests for ralph/workspace/fs.py — Filesystem workspace implementation."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.workspace.fs import FsWorkspace

if TYPE_CHECKING:
    from pathlib import Path


class TestFsWorkspaceRemove:
    def test_removes_existing_file(self, tmp_path: Path) -> None:
        ws = FsWorkspace(tmp_path)
        (tmp_path / "todelete.txt").write_text("remove me", encoding="utf-8")

        ws.remove("todelete.txt")

        assert (tmp_path / "todelete.txt").exists() is False

    def test_remove_nonexistent_does_not_raise(self, tmp_path: Path) -> None:
        ws = FsWorkspace(tmp_path)

        ws.remove("nonexistent.txt")
