"""Tests for ralph/workspace/memory.py — In-memory workspace implementation."""

from __future__ import annotations

from ralph.workspace.memory import MemoryWorkspace


class TestMemoryWorkspaceInit:
    def test_default_initialization(self) -> None:
        ws = MemoryWorkspace()
        assert ws._storage == {}
        assert ws._dirs == {""}

    def test_custom_root(self) -> None:
        ws = MemoryWorkspace("/custom/root")
        assert ws._root.name == "root"
