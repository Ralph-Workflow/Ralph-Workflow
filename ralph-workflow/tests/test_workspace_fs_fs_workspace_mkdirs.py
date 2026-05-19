"""Tests for ralph/workspace/fs.py — Filesystem workspace implementation."""

from __future__ import annotations

import tempfile
from pathlib import Path

from ralph.workspace.fs import FsWorkspace


class TestFsWorkspaceMkdirs:
    def test_mkdirs_creates_nested_directories(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = FsWorkspace(tmpdir)

            ws.mkdirs("a/b/c")

            assert (Path(tmpdir) / "a" / "b" / "c").is_dir()

    def test_mkdirs_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = FsWorkspace(tmpdir)

            ws.mkdirs("a/b")
            ws.mkdirs("a/b")

            assert (Path(tmpdir) / "a" / "b").is_dir()
