"""Tests for ralph/workspace/fs.py — Filesystem workspace implementation."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from ralph.workspace.fs import FsWorkspace


class TestFsWorkspaceAbsolutePath:
    def test_returns_absolute_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = FsWorkspace(tmpdir)

            abs_path = ws.absolute_path("some/file.txt")

            assert abs_path.startswith(str(Path(tmpdir).resolve()))
            assert "some/file.txt" in abs_path

    def test_absolute_path_rejects_parent_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = FsWorkspace(tmpdir)

            with pytest.raises(ValueError, match="outside workspace"):
                ws.absolute_path("../escape.txt")
