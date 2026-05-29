"""Tests for ralph/workspace/fs.py — Filesystem workspace implementation."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.workspace.fs import FsWorkspace

if TYPE_CHECKING:
    from pathlib import Path


class TestFsWorkspaceExists:
    def test_returns_true_for_existing_file(self, tmp_path: Path) -> None:
        ws = FsWorkspace(tmp_path)
        (tmp_path / "exists.txt").write_text("hi", encoding="utf-8")

        assert ws.exists("exists.txt") is True

    def test_returns_false_for_nonexistent(self, tmp_path: Path) -> None:
        ws = FsWorkspace(tmp_path)

        assert ws.exists("missing.txt") is False
