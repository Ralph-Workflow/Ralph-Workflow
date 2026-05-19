"""Tests for ralph/workspace/memory.py — In-memory workspace implementation."""

from __future__ import annotations

from ralph.workspace.memory import MemoryWorkspace


class TestMemoryWorkspaceListDir:
    def test_list_empty_directory(self) -> None:
        ws = MemoryWorkspace()

        entries = ws.list_dir("")
        assert entries == []

    def test_list_files_in_root(self) -> None:
        ws = MemoryWorkspace()
        ws.write("a.txt", "")
        ws.write("b.txt", "")

        entries = ws.list_dir("")
        assert "a.txt" in entries
        assert "b.txt" in entries

    def test_list_nested_files(self) -> None:
        ws = MemoryWorkspace()
        ws.write("dir/file.txt", "")

        entries = ws.list_dir("")
        assert "dir" in entries

    def test_list_empty_subdirectory(self) -> None:
        ws = MemoryWorkspace()
        ws.create_dir("empty_dir")

        entries = ws.list_dir("")
        assert "empty_dir" in entries
