"""Tests for ralph/workspace/fs.py — Filesystem workspace implementation."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from ralph.workspace.fs import FsWorkspace

if TYPE_CHECKING:
    from pathlib import Path


class TestFsWorkspaceRead:
    def test_reads_file_content(self, tmp_path: Path) -> None:
        ws = FsWorkspace(tmp_path)
        (tmp_path / "test.txt").write_text("hello world", encoding="utf-8")

        content = ws.read("test.txt")
        assert content == "hello world"

    def test_read_nonexistent_raises(self, tmp_path: Path) -> None:
        ws = FsWorkspace(tmp_path)

        with pytest.raises(FileNotFoundError):
            ws.read("nonexistent.txt")

    def test_read_with_subdirectory(self, tmp_path: Path) -> None:
        ws = FsWorkspace(tmp_path)
        subdir = tmp_path / "sub" / "dir"
        subdir.mkdir(parents=True)
        (subdir / "file.txt").write_text("nested", encoding="utf-8")

        content = ws.read("sub/dir/file.txt")
        assert content == "nested"
