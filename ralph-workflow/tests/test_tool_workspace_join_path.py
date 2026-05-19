"""Tests for ralph/mcp/tool_workspace.py — MCP workspace tool handlers."""

from __future__ import annotations

from ralph.mcp.tools.workspace import (
    join_path,
)

MEDIA_READ_CAPABILITY = "media.read"
DEFAULT_MAX_INLINE_BYTES = 5_242_880


class TestJoinPath:
    def test_empty_base_returns_normalized_entry(self) -> None:
        assert join_path("", "file.txt") == "file.txt"

    def test_joins_with_posix_separator(self) -> None:
        assert join_path("dir", "file.txt") == "dir/file.txt"

    def test_multiple_segments(self) -> None:
        assert join_path("a/b", "c/d") == "a/b/c/d"
