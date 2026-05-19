"""Tests for ralph/workspace/fs.py — Filesystem workspace implementation."""

from __future__ import annotations

import tempfile
from pathlib import Path

from ralph.workspace.fs import FsWorkspace


class TestFsWorkspaceListDir:
    def test_lists_directory_contents(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = FsWorkspace(tmpdir)
            (Path(tmpdir) / "a.txt").write_text("", encoding="utf-8")
            (Path(tmpdir) / "b.txt").write_text("", encoding="utf-8")

            entries = ws.list_dir(".")

            assert "a.txt" in entries
            assert "b.txt" in entries

    def test_list_dir_empty_for_nonexistent(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = FsWorkspace(tmpdir)

            entries = ws.list_dir("nonexistent")

            assert entries == []
