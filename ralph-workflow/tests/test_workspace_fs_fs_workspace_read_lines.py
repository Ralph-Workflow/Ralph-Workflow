"""Tests for ralph/workspace/fs.py — Filesystem workspace implementation."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from ralph.workspace.fs import FsWorkspace

if TYPE_CHECKING:
    from pathlib import Path


class TestFsWorkspaceReadLines:
    def test_read_lines_returns_all_lines(self, tmp_path: Path) -> None:
        ws = FsWorkspace(tmp_path)
        (tmp_path / "lines.txt").write_text("line1\nline2\nline3\n", encoding="utf-8")

        content, meta = ws.read_lines("lines.txt")
        assert content == "line1\nline2\nline3\n"
        assert meta["total_lines"] == 3
        assert meta["returned_lines"] == 3
        assert meta["truncated"] is False

    def test_read_lines_head_returns_first_n(self, tmp_path: Path) -> None:
        ws = FsWorkspace(tmp_path)
        (tmp_path / "lines.txt").write_text("line1\nline2\nline3\n", encoding="utf-8")

        content, meta = ws.read_lines("lines.txt", head=2)
        assert content == "line1\nline2\n"
        assert meta["returned_lines"] == 2
        assert meta["truncated"] is True

    def test_read_lines_tail_returns_last_n(self, tmp_path: Path) -> None:
        ws = FsWorkspace(tmp_path)
        (tmp_path / "lines.txt").write_text("line1\nline2\nline3\n", encoding="utf-8")

        content, meta = ws.read_lines("lines.txt", tail=2)
        assert content == "line2\nline3\n"
        assert meta["returned_lines"] == 2
        assert meta["truncated"] is True

    def test_read_lines_start_end_returns_range(self, tmp_path: Path) -> None:
        ws = FsWorkspace(tmp_path)
        (tmp_path / "lines.txt").write_text("line1\nline2\nline3\n", encoding="utf-8")

        content, meta = ws.read_lines("lines.txt", start=2, end=3)
        assert content == "line2\nline3\n"
        assert meta["total_lines"] == 3
        assert meta["returned_lines"] == 2

    def test_read_lines_conflicting_params_raises(self, tmp_path: Path) -> None:
        ws = FsWorkspace(tmp_path)
        (tmp_path / "lines.txt").write_text("line1\nline2\n", encoding="utf-8")

        with pytest.raises(ValueError, match="Only one of"):
            ws.read_lines("lines.txt", head=2, tail=2)

    def test_read_lines_nonexistent_raises(self, tmp_path: Path) -> None:
        ws = FsWorkspace(tmp_path)

        with pytest.raises(FileNotFoundError):
            ws.read_lines("missing.txt")
