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

    def test_read_lines_no_trailing_newline_full_file(self, tmp_path: Path) -> None:
        ws = FsWorkspace(tmp_path)
        (tmp_path / "sample.txt").write_text("alpha\nbeta", encoding="utf-8")

        content, meta = ws.read_lines("sample.txt")
        assert content == "alpha\nbeta"
        assert meta["total_lines"] == 2
        assert meta["returned_lines"] == 2
        assert meta["truncated"] is False

    def test_read_lines_empty_file(self, tmp_path: Path) -> None:
        ws = FsWorkspace(tmp_path)
        (tmp_path / "empty.txt").write_text("", encoding="utf-8")

        content, meta = ws.read_lines("empty.txt")
        assert content == ""
        assert meta["total_lines"] == 0
        assert meta["returned_lines"] == 0
        assert meta["truncated"] is False

    def test_read_lines_single_line_no_newline(self, tmp_path: Path) -> None:
        ws = FsWorkspace(tmp_path)
        (tmp_path / "lonely.txt").write_text("lonely", encoding="utf-8")

        content, meta = ws.read_lines("lonely.txt")
        assert content == "lonely"
        assert meta["total_lines"] == 1
        assert meta["returned_lines"] == 1
        assert meta["truncated"] is False

    def test_read_lines_no_trailing_newline_head(self, tmp_path: Path) -> None:
        ws = FsWorkspace(tmp_path)
        (tmp_path / "sample.txt").write_text("alpha\nbeta\ngamma", encoding="utf-8")

        content, meta = ws.read_lines("sample.txt", head=2)
        assert content == "alpha\nbeta\n"
        assert meta["total_lines"] == 3
        assert meta["returned_lines"] == 2
        assert meta["truncated"] is True

    def test_read_lines_no_trailing_newline_tail(self, tmp_path: Path) -> None:
        ws = FsWorkspace(tmp_path)
        (tmp_path / "sample.txt").write_text("alpha\nbeta\ngamma", encoding="utf-8")

        content, meta = ws.read_lines("sample.txt", tail=2)
        assert content == "beta\ngamma"
        assert meta["total_lines"] == 3
        assert meta["returned_lines"] == 2
        assert meta["truncated"] is True

    def test_read_lines_no_trailing_newline_range(self, tmp_path: Path) -> None:
        ws = FsWorkspace(tmp_path)
        (tmp_path / "sample.txt").write_text("alpha\nbeta\ngamma", encoding="utf-8")

        content, meta = ws.read_lines("sample.txt", start=2, end=3)
        assert content == "beta\ngamma"
        assert meta["total_lines"] == 3
        assert meta["returned_lines"] == 2
        assert meta["truncated"] is False

    def test_read_lines_count_lines_helper_no_trailing_newline(self, tmp_path: Path) -> None:
        """Direct regression for the prior analysis-feedback bug.

        ``_count_lines`` MUST add 1 for a final unterminated line so
        that ``total_lines`` matches the number of lines actually
        returned by ``read_lines`` for files without a trailing
        newline. An empty file still reports 0.
        """
        ws = FsWorkspace(tmp_path)
        alpha_beta = tmp_path / "alpha_beta.txt"
        alpha_beta.write_text("alpha\nbeta", encoding="utf-8")
        assert ws._count_lines(alpha_beta) == 2

        empty = tmp_path / "empty.txt"
        empty.write_text("", encoding="utf-8")
        assert ws._count_lines(empty) == 0

        with_newline = tmp_path / "with_newline.txt"
        with_newline.write_text("alpha\nbeta\n", encoding="utf-8")
        assert ws._count_lines(with_newline) == 2

        single_no_newline = tmp_path / "lonely.txt"
        single_no_newline.write_text("lonely", encoding="utf-8")
        assert ws._count_lines(single_no_newline) == 1

        crlf_no_trailing = tmp_path / "crlf.txt"
        crlf_no_trailing.write_bytes(b"alpha\r\nbeta")
        assert ws._count_lines(crlf_no_trailing) == 2
