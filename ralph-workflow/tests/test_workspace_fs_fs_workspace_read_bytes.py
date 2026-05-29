"""Tests for ralph/workspace/fs.py — Filesystem workspace implementation."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from ralph.workspace.fs import FsWorkspace

if TYPE_CHECKING:
    from pathlib import Path


class TestFsWorkspaceReadBytes:
    def test_full_file_read_no_offset(self, tmp_path: Path) -> None:
        ws = FsWorkspace(tmp_path)
        content = "Hello, World!"
        (tmp_path / "file.txt").write_text(content, encoding="utf-8")

        text, meta = ws.read_bytes("file.txt")

        assert text == content
        assert meta["total_bytes"] == len(content.encode("utf-8"))
        assert meta["returned_bytes"] == len(content.encode("utf-8"))
        assert meta["truncated"] is False

    def test_byte_offset_read(self, tmp_path: Path) -> None:
        ws = FsWorkspace(tmp_path)
        (tmp_path / "file.txt").write_text("Hello, World!", encoding="utf-8")

        text, meta = ws.read_bytes("file.txt", offset=7)

        assert text == "World!"
        assert meta["returned_bytes"] == len(b"World!")
        assert meta["truncated"] is False

    def test_byte_limit_read(self, tmp_path: Path) -> None:
        ws = FsWorkspace(tmp_path)
        (tmp_path / "file.txt").write_text("Hello, World!", encoding="utf-8")

        text, meta = ws.read_bytes("file.txt", limit=5)

        assert text == "Hello"
        assert meta["returned_bytes"] == 5
        assert meta["truncated"] is True

    def test_offset_and_limit_read(self, tmp_path: Path) -> None:
        ws = FsWorkspace(tmp_path)
        (tmp_path / "file.txt").write_text("Hello, World!", encoding="utf-8")

        text, meta = ws.read_bytes("file.txt", offset=7, limit=5)

        assert text == "World"
        assert meta["returned_bytes"] == 5
        assert meta["truncated"] is True

    def test_missing_file_raises_file_not_found(self, tmp_path: Path) -> None:
        ws = FsWorkspace(tmp_path)

        with pytest.raises(FileNotFoundError):
            ws.read_bytes("nonexistent.txt")

    def test_total_bytes_reflects_file_size(self, tmp_path: Path) -> None:
        ws = FsWorkspace(tmp_path)
        content = "A" * 1000
        (tmp_path / "large.txt").write_text(content, encoding="utf-8")

        _, meta = ws.read_bytes("large.txt", limit=100)

        assert meta["total_bytes"] == 1000
        assert meta["returned_bytes"] == 100
        assert meta["truncated"] is True
