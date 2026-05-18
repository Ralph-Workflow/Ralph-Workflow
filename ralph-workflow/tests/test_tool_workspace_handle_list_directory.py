"""Tests for ralph/mcp/tool_workspace.py — MCP workspace tool handlers."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast
from unittest.mock import MagicMock

from ralph.mcp.tools.workspace import (
    WORKSPACE_READ_CAPABILITY,
    handle_list_directory,
)

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


    class MockSession:
        session_id = "test-session"

        def __init__(self, *args: object) -> None:
            if not args:
                self._caps: set[str] = set()
            elif len(args) == 1 and isinstance(args[0], set):
                self._caps = {s for s in args[0] if isinstance(s, str)}
            else:
                self._caps = {s for s in args if isinstance(s, str)}

        def check_capability(self, capability: str) -> object:
            return capability in self._caps


MockSession = TestHandleListDirectory.MockSession
