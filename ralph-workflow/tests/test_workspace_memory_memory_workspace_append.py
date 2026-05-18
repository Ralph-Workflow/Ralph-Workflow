"""Tests for ralph/workspace/memory.py — In-memory workspace implementation."""

from __future__ import annotations

from ralph.workspace.memory import MemoryWorkspace


class TestMemoryWorkspaceAppend:
    def test_append_to_existing(self) -> None:
        ws = MemoryWorkspace()
        ws.write("file.txt", "start")

        ws.append("file.txt", "_end")

        assert ws.read("file.txt") == "start_end"

    def test_append_creates_new_file(self) -> None:
        ws = MemoryWorkspace()
        ws.append("new.txt", "first")

        assert ws.read("new.txt") == "first"
