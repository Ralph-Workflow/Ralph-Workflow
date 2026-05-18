"""Tests for ralph/workspace/memory.py — In-memory workspace implementation."""

from __future__ import annotations

import pytest

from ralph.workspace.memory import MemoryWorkspace


class TestMemoryWorkspaceReadBytes:
    def test_full_read_returns_all_content(self) -> None:
        ws = MemoryWorkspace()
        ws.write("file.txt", "Hello, World!")

        text, meta = ws.read_bytes("file.txt")

        assert text == "Hello, World!"
        assert meta["total_bytes"] == len(b"Hello, World!")
        assert meta["returned_bytes"] == meta["total_bytes"]
        assert meta["truncated"] is False

    def test_offset_skips_leading_bytes(self) -> None:
        ws = MemoryWorkspace()
        ws.write("file.txt", "Hello, World!")

        text, meta = ws.read_bytes("file.txt", offset=7)

        assert text == "World!"
        assert meta["truncated"] is False

    def test_limit_truncates_content(self) -> None:
        ws = MemoryWorkspace()
        ws.write("file.txt", "Hello, World!")

        text, meta = ws.read_bytes("file.txt", limit=5)

        assert text == "Hello"
        assert meta["returned_bytes"] == 5
        assert meta["truncated"] is True

    def test_offset_and_limit_together(self) -> None:
        ws = MemoryWorkspace()
        ws.write("file.txt", "Hello, World!")

        text, meta = ws.read_bytes("file.txt", offset=7, limit=5)

        assert text == "World"
        assert meta["truncated"] is True

    def test_missing_file_raises_file_not_found(self) -> None:
        ws = MemoryWorkspace()

        with pytest.raises(FileNotFoundError):
            ws.read_bytes("nonexistent.txt")
