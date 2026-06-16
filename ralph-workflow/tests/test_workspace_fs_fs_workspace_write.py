"""Tests for ralph/workspace/fs.py — Filesystem workspace implementation."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from ralph.workspace.fs import FsWorkspace

if TYPE_CHECKING:
    from pathlib import Path


class TestFsWorkspaceWrite:
    def test_writes_file_content(self, tmp_path: Path) -> None:
        ws = FsWorkspace(tmp_path)

        ws.write("output.txt", "test content")

        assert (tmp_path / "output.txt").read_text(encoding="utf-8") == "test content"

    def test_writes_creates_parent_directories(self, tmp_path: Path) -> None:
        ws = FsWorkspace(tmp_path)

        ws.write("a/b/c/deep.txt", "content")

        assert (tmp_path / "a" / "b" / "c" / "deep.txt").read_text(encoding="utf-8") == "content"

    def test_writes_overwrites_existing(self, tmp_path: Path) -> None:
        ws = FsWorkspace(tmp_path)
        (tmp_path / "existing.txt").write_text("original", encoding="utf-8")

        ws.write("existing.txt", "updated")

        assert (tmp_path / "existing.txt").read_text(encoding="utf-8") == "updated"

    def test_write_rejects_parent_traversal_outside_workspace(self, tmp_path: Path) -> None:
        ws = FsWorkspace(tmp_path)

        with pytest.raises(ValueError, match="outside workspace"):
            ws.write("../escape.txt", "blocked")

    def test_write_rejects_absolute_path_outside_workspace(self, tmp_path: Path) -> None:
        ws = FsWorkspace(tmp_path)

        with pytest.raises(ValueError, match="outside workspace"):
            ws.write("/tmp/escape.txt", "blocked")
