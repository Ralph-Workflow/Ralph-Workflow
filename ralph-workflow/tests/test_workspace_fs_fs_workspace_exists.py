"""Tests for ralph/workspace/fs.py — Filesystem workspace implementation."""

from __future__ import annotations

import tempfile
from pathlib import Path

from ralph.workspace.fs import FsWorkspace


class TestFsWorkspaceExists:
    def test_returns_true_for_existing_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = FsWorkspace(tmpdir)
            (Path(tmpdir) / "exists.txt").write_text("hi", encoding="utf-8")

            assert ws.exists("exists.txt") is True

    def test_returns_false_for_nonexistent(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = FsWorkspace(tmpdir)

            assert ws.exists("missing.txt") is False
