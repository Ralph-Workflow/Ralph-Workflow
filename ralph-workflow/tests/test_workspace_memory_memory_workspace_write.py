"""Tests for ralph/workspace/memory.py — In-memory workspace implementation."""

from __future__ import annotations

from ralph.workspace.memory import MemoryWorkspace


class TestMemoryWorkspaceWrite:
    def test_write_creates_file(self) -> None:
        ws = MemoryWorkspace()
        ws.write("new.txt", "hello")

        assert ws.read("new.txt") == "hello"

    def test_write_overwrites(self) -> None:
        ws = MemoryWorkspace()
        ws.write("file.txt", "v1")
        ws.write("file.txt", "v2")

        assert ws.read("file.txt") == "v2"

    def test_write_creates_parent_dirs(self) -> None:
        ws = MemoryWorkspace()
        ws.write("a/b/c/deep.txt", "content")

        assert ws.read("a/b/c/deep.txt") == "content"
        assert ws.is_dir("a/b/c")
