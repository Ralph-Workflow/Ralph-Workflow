"""Tests for ralph/workspace/memory.py — In-memory workspace implementation."""

from __future__ import annotations

from ralph.workspace.memory import MemoryWorkspace


class TestMemoryWorkspaceIsFile:
    def test_is_file_true_for_file(self) -> None:
        ws = MemoryWorkspace()
        ws.write("file.txt", "")

        assert ws.is_file("file.txt") is True

    def test_is_file_false_for_directory(self) -> None:
        ws = MemoryWorkspace()
        ws.create_dir("mydir")

        assert ws.is_file("mydir") is False

    def test_is_file_false_for_nonexistent(self) -> None:
        ws = MemoryWorkspace()

        assert ws.is_file("missing.txt") is False
