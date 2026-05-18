"""Tests for ralph/workspace/memory.py — In-memory workspace implementation."""

from __future__ import annotations

from ralph.workspace.memory import MemoryWorkspace


class TestMemoryWorkspaceExists:
    def test_exists_for_written_file(self) -> None:
        ws = MemoryWorkspace()
        ws.write("file.txt", "content")

        assert ws.exists("file.txt") is True

    def test_not_exists_for_missing(self) -> None:
        ws = MemoryWorkspace()

        assert ws.exists("missing.txt") is False
