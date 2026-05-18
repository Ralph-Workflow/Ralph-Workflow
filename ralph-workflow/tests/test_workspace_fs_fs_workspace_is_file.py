"""Tests for ralph/workspace/fs.py — Filesystem workspace implementation."""

from __future__ import annotations

import tempfile
from pathlib import Path

from ralph.workspace.fs import FsWorkspace


class TestFsWorkspaceIsFile:
    def test_returns_true_for_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = FsWorkspace(tmpdir)
            (Path(tmpdir) / "file.txt").write_text("", encoding="utf-8")

            assert ws.is_file("file.txt") is True

    def test_returns_false_for_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = FsWorkspace(tmpdir)
            (Path(tmpdir) / "subdir").mkdir()

            assert ws.is_file("subdir") is False
