"""Tests for ralph/workspace/fs.py — Filesystem workspace implementation."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from ralph.workspace.fs import FsWorkspace


class TestFsWorkspaceWrite:
    def test_writes_file_content(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = FsWorkspace(tmpdir)

            ws.write("output.txt", "test content")

            assert (Path(tmpdir) / "output.txt").read_text(encoding="utf-8") == "test content"

    def test_writes_creates_parent_directories(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = FsWorkspace(tmpdir)

            ws.write("a/b/c/deep.txt", "content")

            assert (Path(tmpdir) / "a" / "b" / "c" / "deep.txt").read_text(
                encoding="utf-8"
            ) == "content"

    def test_writes_overwrites_existing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = FsWorkspace(tmpdir)
            (Path(tmpdir) / "existing.txt").write_text("original", encoding="utf-8")

            ws.write("existing.txt", "updated")

            assert (Path(tmpdir) / "existing.txt").read_text(encoding="utf-8") == "updated"

    def test_write_rejects_parent_traversal_outside_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = FsWorkspace(tmpdir)

            with pytest.raises(ValueError, match="outside workspace"):
                ws.write("../escape.txt", "blocked")

    def test_write_rejects_absolute_path_outside_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = FsWorkspace(tmpdir)

            with pytest.raises(ValueError, match="outside workspace"):
                ws.write("/tmp/escape.txt", "blocked")
