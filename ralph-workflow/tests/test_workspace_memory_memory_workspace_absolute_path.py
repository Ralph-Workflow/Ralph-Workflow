"""Tests for ralph/workspace/memory.py — In-memory workspace implementation."""

from __future__ import annotations

from ralph.workspace.memory import MemoryWorkspace


class TestMemoryWorkspaceAbsolutePath:
    def test_returns_absolute_path(self) -> None:
        ws = MemoryWorkspace("/workspace")

        abs_path = ws.absolute_path("file.txt")

        assert "file.txt" in abs_path
        assert ws._root.name in abs_path
