"""Tests for ralph/workspace/fs.py — Filesystem workspace implementation."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from ralph.workspace.fs import FsWorkspace


class TestFsWorkspaceRead:
    def test_reads_file_content(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = FsWorkspace(tmpdir)
            (Path(tmpdir) / "test.txt").write_text("hello world", encoding="utf-8")

            content = ws.read("test.txt")
            assert content == "hello world"

    def test_read_nonexistent_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = FsWorkspace(tmpdir)

            with pytest.raises(FileNotFoundError):
                ws.read("nonexistent.txt")

    def test_read_with_subdirectory(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = FsWorkspace(tmpdir)
            subdir = Path(tmpdir) / "sub" / "dir"
            subdir.mkdir(parents=True)
            (subdir / "file.txt").write_text("nested", encoding="utf-8")

            content = ws.read("sub/dir/file.txt")
            assert content == "nested"
