"""Tests for ralph/workspace/fs.py — Filesystem workspace implementation."""

from __future__ import annotations

import tempfile
from pathlib import Path

from ralph.workspace.fs import FsWorkspace


class TestFsWorkspaceRoot:
    def test_returns_root_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = FsWorkspace(tmpdir)

            assert ws.root == Path(tmpdir).resolve()
