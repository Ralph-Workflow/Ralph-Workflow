"""Tests for ralph/mcp/tool_workspace.py — MCP workspace tool handlers."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast
from unittest.mock import MagicMock

from ralph.mcp.tools.workspace import (
    WORKSPACE_READ_CAPABILITY,
    handle_list_directory,
)
from tests.mock_session import MockSession

if TYPE_CHECKING:
    from ralph.mcp.tools.coordination import (
        ToolContent,
    )

MEDIA_READ_CAPABILITY = "media.read"
DEFAULT_MAX_INLINE_BYTES = 5_242_880


class TestHandleListDirectory:
    def test_lists_directory_flat(self) -> None:
        ws = MagicMock()
        ws.list_dir.return_value = ["a.txt", "b.txt"]
        ws.is_dir.side_effect = lambda p: False

        result = handle_list_directory(MockSession(WORKSPACE_READ_CAPABILITY), ws, {"path": "."})
        assert result.is_error is False
        assert "Directory:" in cast("ToolContent", result.content[0]).text

    def test_lists_directory_recursive(self) -> None:
        ws = MagicMock()
        ws.list_dir.return_value = []
        ws.is_dir.side_effect = lambda p: False

        result = handle_list_directory(
            MockSession(WORKSPACE_READ_CAPABILITY),
            ws,
            {"path": ".", "recursive": True},
        )
        assert result.is_error is False
        assert "Directory (recursive):" in cast("ToolContent", result.content[0]).text
