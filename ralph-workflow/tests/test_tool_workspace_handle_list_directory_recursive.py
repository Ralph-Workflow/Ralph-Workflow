"""Tests for ralph/mcp/tool_workspace.py — MCP workspace tool handlers."""

from __future__ import annotations
from tests.mock_session import MockSession

from typing import TYPE_CHECKING, cast
from unittest.mock import MagicMock

from ralph.mcp.tools.workspace import (
    WORKSPACE_READ_CAPABILITY,
    handle_list_directory_recursive,
)

if TYPE_CHECKING:
    from ralph.mcp.tools.coordination import (
        ToolContent,
    )

MEDIA_READ_CAPABILITY = "media.read"
DEFAULT_MAX_INLINE_BYTES = 5_242_880


class TestHandleListDirectoryRecursive:
    def test_returns_recursive_listing(self) -> None:
        ws = MagicMock()
        ws.list_dir.return_value = []
        ws.is_dir.side_effect = lambda p: False

        result = handle_list_directory_recursive(
            MockSession(WORKSPACE_READ_CAPABILITY),
            ws,
            {"path": "."},
        )
        assert result.is_error is False
        assert "Directory (recursive):" in cast("ToolContent", result.content[0]).text

    def test_skips_heavy_directories_and_nested_worktrees(self) -> None:
        ws = MagicMock()
        listings = {
            "": [".git", "src", "target", "wt-feature"],
            "src": ["main.py"],
            ".git": ["objects"],
            "target": ["debug"],
            "wt-feature": ["scratch.txt"],
        }
        directories = {
            ".git",
            ".git/objects",
            "src",
            "target",
            "target/debug",
            "wt-feature",
        }

        ws.list_dir.side_effect = lambda path: listings.get(path, [])
        ws.is_dir.side_effect = lambda path: path in directories
        ws.exists.side_effect = lambda path: path == "wt-feature/.git"

        result = handle_list_directory_recursive(
            MockSession(WORKSPACE_READ_CAPABILITY),
            ws,
            {"path": "."},
        )

        text = cast("ToolContent", result.content[0]).text
        assert "src/main.py" in text
        assert ".git/objects" not in text
        assert "target/debug" not in text
        assert "wt-feature/scratch.txt" not in text


