"""Tests for ralph/workspace/memory.py — In-memory workspace implementation."""

from __future__ import annotations

from ralph.workspace.memory import MemoryWorkspace


class TestMemoryWorkspaceNormalize:
    def test_empty_string(self) -> None:
        ws = MemoryWorkspace()
        assert ws._normalize("") == ""

    def test_dot_becomes_empty(self) -> None:
        ws = MemoryWorkspace()
        assert ws._normalize(".") == ""

    def test_path_normalization(self) -> None:
        ws = MemoryWorkspace()
        assert ws._normalize("a/b/c") == "a/b/c"
