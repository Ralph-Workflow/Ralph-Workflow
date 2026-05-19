"""Tests for ralph/workspace/fs.py — Filesystem workspace implementation."""

from __future__ import annotations

import tempfile

from ralph.workspace.fs import FsWorkspace


class TestFsWorkspaceAllowedRoots:
    def test_allowed_roots_returns_list(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = FsWorkspace(tmpdir)

            roots = ws.allowed_roots()

            assert isinstance(roots, list)
            assert len(roots) >= 1

    def test_allowed_roots_with_multiple_roots(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = FsWorkspace(tmpdir, allowed_roots=[tmpdir, "/tmp"])

            roots = ws.allowed_roots()

            assert len(roots) == 2
