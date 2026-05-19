"""Tests for ralph/mcp/tool_workspace.py — MCP workspace tool handlers."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from ralph.mcp.tools.coordination import (
    ToolError,
)
from ralph.mcp.tools.workspace import (
    list_dir_entries,
)

MEDIA_READ_CAPABILITY = "media.read"
DEFAULT_MAX_INLINE_BYTES = 5_242_880


class TestListDirEntries:
    def test_returns_list_from_workspace(self) -> None:
        ws = MagicMock()
        ws.list_dir.return_value = ["file1.txt", "file2.txt"]

        result = list_dir_entries(ws, "")
        assert result == ["file1.txt", "file2.txt"]

    def test_propagates_workspace_exception(self) -> None:
        ws = MagicMock()
        ws.list_dir.side_effect = RuntimeError("disk error")

        with pytest.raises(ToolError):
            list_dir_entries(ws, "")
