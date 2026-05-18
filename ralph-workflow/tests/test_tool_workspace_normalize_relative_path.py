"""Tests for ralph/mcp/tool_workspace.py — MCP workspace tool handlers."""

from __future__ import annotations

from ralph.mcp.tools.workspace import (
    normalize_relative_path,
)

MEDIA_READ_CAPABILITY = "media.read"
DEFAULT_MAX_INLINE_BYTES = 5_242_880


class TestNormalizeRelativePath:
    def test_empty_path_returns_empty(self) -> None:
        assert normalize_relative_path("") == ""

    def test_dot_returns_empty(self) -> None:
        assert normalize_relative_path(".") == ""

    def test_slash_handled(self) -> None:
        result = normalize_relative_path("path/to/file")
        assert result == "path/to/file"

    def test_windows_backslash_preserved(self) -> None:
        result = normalize_relative_path("path\\to\\file")
        assert result == "path\\to\\file"

    def test_leading_slash_preserved(self) -> None:
        result = normalize_relative_path("/absolute/path")
        assert result.startswith("/")
