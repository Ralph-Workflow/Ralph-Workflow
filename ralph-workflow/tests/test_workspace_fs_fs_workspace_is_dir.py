"""Tests for ralph/workspace/fs.py — Filesystem workspace implementation."""

from __future__ import annotations

import tempfile
from pathlib import Path

from ralph.workspace.fs import FsWorkspace


class TestFsWorkspaceIsDir:
    def test_returns_true_for_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = FsWorkspace(tmpdir)
            (Path(tmpdir) / "subdir").mkdir()

            assert ws.is_dir("subdir") is True

    def test_returns_false_for_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = FsWorkspace(tmpdir)
            (Path(tmpdir) / "afile.txt").write_text("", encoding="utf-8")

            assert ws.is_dir("afile.txt") is False
