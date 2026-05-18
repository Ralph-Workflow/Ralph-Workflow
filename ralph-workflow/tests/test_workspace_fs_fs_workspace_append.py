"""Tests for ralph/workspace/fs.py — Filesystem workspace implementation."""

from __future__ import annotations

import tempfile
from pathlib import Path

from ralph.workspace.fs import FsWorkspace


class TestFsWorkspaceAppend:
    def test_appends_to_existing_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = FsWorkspace(tmpdir)
            (Path(tmpdir) / "append.txt").write_text("start", encoding="utf-8")

            ws.append("append.txt", "_end")

            assert (Path(tmpdir) / "append.txt").read_text(encoding="utf-8") == "start_end"

    def test_append_creates_new_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = FsWorkspace(tmpdir)

            ws.append("new.txt", "first")

            assert (Path(tmpdir) / "new.txt").read_text(encoding="utf-8") == "first"

    def test_append_creates_parent_directories(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = FsWorkspace(tmpdir)

            ws.append("x/y/file.txt", "content")

            assert (Path(tmpdir) / "x" / "y" / "file.txt").read_text(encoding="utf-8") == "content"
