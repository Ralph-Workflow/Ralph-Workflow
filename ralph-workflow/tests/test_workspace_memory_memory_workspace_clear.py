"""Tests for ralph/workspace/memory.py — In-memory workspace implementation."""

from __future__ import annotations

from ralph.workspace.memory import MemoryWorkspace


class TestMemoryWorkspaceClear:
    def test_clear_removes_all_files(self) -> None:
        ws = MemoryWorkspace()
        ws.write("a.txt", "")
        ws.write("b.txt", "")

        ws.clear()

        assert ws.exists("a.txt") is False
        assert ws.exists("b.txt") is False
