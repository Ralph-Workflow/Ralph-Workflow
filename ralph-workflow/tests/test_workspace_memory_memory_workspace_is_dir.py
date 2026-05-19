"""Tests for ralph/workspace/memory.py — In-memory workspace implementation."""

from __future__ import annotations

from ralph.workspace.memory import MemoryWorkspace


class TestMemoryWorkspaceIsDir:
    def test_is_dir_for_created_directory(self) -> None:
        ws = MemoryWorkspace()
        ws.create_dir("mydir")

        assert ws.is_dir("mydir") is True

    def test_is_dir_false_for_file(self) -> None:
        ws = MemoryWorkspace()
        ws.write("file.txt", "")

        assert ws.is_dir("file.txt") is False

    def test_is_dir_false_for_nonexistent(self) -> None:
        ws = MemoryWorkspace()

        assert ws.is_dir("missing") is False
