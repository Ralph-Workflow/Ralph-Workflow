"""Tests for ralph/workspace/fs.py — Filesystem workspace implementation."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.workspace.fs import FsWorkspace

if TYPE_CHECKING:
    from pathlib import Path


class TestFsWorkspaceAppend:
    def test_appends_to_existing_file(self, tmp_path: Path) -> None:
        ws = FsWorkspace(tmp_path)
        (tmp_path / "append.txt").write_text("start", encoding="utf-8")

        ws.append("append.txt", "_end")

        assert (tmp_path / "append.txt").read_text(encoding="utf-8") == "start_end"

    def test_append_creates_new_file(self, tmp_path: Path) -> None:
        ws = FsWorkspace(tmp_path)

        ws.append("new.txt", "first")

        assert (tmp_path / "new.txt").read_text(encoding="utf-8") == "first"

    def test_append_creates_parent_directories(self, tmp_path: Path) -> None:
        ws = FsWorkspace(tmp_path)

        ws.append("x/y/file.txt", "content")

        assert (tmp_path / "x" / "y" / "file.txt").read_text(encoding="utf-8") == "content"
