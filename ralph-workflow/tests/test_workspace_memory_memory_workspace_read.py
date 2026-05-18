"""Tests for ralph/workspace/memory.py — In-memory workspace implementation."""

from __future__ import annotations

import pytest

from ralph.workspace.memory import MemoryWorkspace


class TestMemoryWorkspaceRead:
    def test_read_existing_file(self) -> None:
        ws = MemoryWorkspace()
        ws.write("file.txt", "content")

        assert ws.read("file.txt") == "content"

    def test_read_nonexistent_raises(self) -> None:
        ws = MemoryWorkspace()

        with pytest.raises(FileNotFoundError) as exc_info:
            ws.read("missing.txt")
        assert "not found" in str(exc_info.value)
