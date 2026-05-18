"""Tests for ralph/workspace/memory.py — In-memory workspace implementation."""

from __future__ import annotations

from ralph.workspace.memory import MemoryWorkspace


class TestMemoryWorkspaceCreateDir:
    def test_create_dir(self) -> None:
        ws = MemoryWorkspace()
        ws.create_dir("newdir")

        assert ws.is_dir("newdir") is True

    def test_create_nested_dir(self) -> None:
        ws = MemoryWorkspace()
        ws.create_dir("a/b/c")

        assert ws.is_dir("a/b/c") is True
