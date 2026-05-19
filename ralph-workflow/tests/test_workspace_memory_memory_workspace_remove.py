"""Tests for ralph/workspace/memory.py — In-memory workspace implementation."""

from __future__ import annotations

from ralph.workspace.memory import MemoryWorkspace


class TestMemoryWorkspaceRemove:
    def test_remove_existing_file(self) -> None:
        ws = MemoryWorkspace()
        ws.write("todelete.txt", "content")

        ws.remove("todelete.txt")

        assert ws.exists("todelete.txt") is False

    def test_remove_nonexistent_no_error(self) -> None:
        ws = MemoryWorkspace()
        ws.remove("nonexistent.txt")
