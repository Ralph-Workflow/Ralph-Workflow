"""Tests for ralph/workspace/fs.py — Filesystem workspace implementation."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.workspace.fs import FsWorkspace

if TYPE_CHECKING:
    from pathlib import Path


class TestFsWorkspaceIsFile:
    def test_returns_true_for_file(self, tmp_path: Path) -> None:
        ws = FsWorkspace(tmp_path)
        (tmp_path / "file.txt").write_text("", encoding="utf-8")

        assert ws.is_file("file.txt") is True

    def test_returns_false_for_directory(self, tmp_path: Path) -> None:
        ws = FsWorkspace(tmp_path)
        (tmp_path / "subdir").mkdir()

        assert ws.is_file("subdir") is False
