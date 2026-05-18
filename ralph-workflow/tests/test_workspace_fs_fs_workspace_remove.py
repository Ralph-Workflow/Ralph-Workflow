"""Tests for ralph/workspace/fs.py — Filesystem workspace implementation."""

from __future__ import annotations

import tempfile
from pathlib import Path

from ralph.workspace.fs import FsWorkspace


class TestFsWorkspaceRemove:
    def test_removes_existing_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = FsWorkspace(tmpdir)
            (Path(tmpdir) / "todelete.txt").write_text("remove me", encoding="utf-8")

            ws.remove("todelete.txt")

            assert (Path(tmpdir) / "todelete.txt").exists() is False

    def test_remove_nonexistent_does_not_raise(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = FsWorkspace(tmpdir)

            ws.remove("nonexistent.txt")
