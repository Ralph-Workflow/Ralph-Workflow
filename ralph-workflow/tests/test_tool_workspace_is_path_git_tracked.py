"""Tests for ralph/mcp/tool_workspace.py — MCP workspace tool handlers."""

from __future__ import annotations

from unittest.mock import MagicMock

from ralph.mcp.tools.workspace import (
    is_path_git_tracked,
)

MEDIA_READ_CAPABILITY = "media.read"
DEFAULT_MAX_INLINE_BYTES = 5_242_880


class TestIsPathGitTracked:
    def test_empty_path_returns_false(self) -> None:
        ws = MagicMock()
        ws.exists.return_value = False
        assert is_path_git_tracked(ws, "") is False

    def test_nonexistent_path_returns_false(self) -> None:
        ws = MagicMock()
        ws.exists.return_value = False
        assert is_path_git_tracked(ws, "file.txt") is False

    def test_existing_file_not_in_excluded_paths_returns_true(self) -> None:
        ws = MagicMock()
        ws.exists.return_value = True
        assert is_path_git_tracked(ws, "src/main.py") is True

    def test_file_in_agent_dir_returns_false(self) -> None:
        ws = MagicMock()
        ws.exists.return_value = True
        assert is_path_git_tracked(ws, ".agent/config.yaml") is False

    def test_file_with_target_substring_but_not_excluded_path(self) -> None:
        ws = MagicMock()
        ws.exists.return_value = True
        assert is_path_git_tracked(ws, "my_target/main") is True

    def test_file_in_node_modules_returns_false(self) -> None:
        ws = MagicMock()
        ws.exists.return_value = True
        assert is_path_git_tracked(ws, "node_modules/lodash") is False

    def test_backslash_path_normalized(self) -> None:
        ws = MagicMock()
        ws.exists.return_value = True
        result = is_path_git_tracked(ws, "src\\main.py")
        assert result is True
