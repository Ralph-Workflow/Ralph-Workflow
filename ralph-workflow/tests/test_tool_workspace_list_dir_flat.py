"""Tests for ralph/mcp/tool_workspace.py — MCP workspace tool handlers."""

from __future__ import annotations

from unittest.mock import MagicMock

from ralph.mcp.tools.workspace import (
    list_dir_flat,
)

MEDIA_READ_CAPABILITY = "media.read"
DEFAULT_MAX_INLINE_BYTES = 5_242_880


class TestListDirFlat:
    def test_lists_files_and_dirs(self) -> None:
        ws = MagicMock()
        ws.list_dir.return_value = ["file.txt", "subdir"]
        ws.is_dir.side_effect = lambda p: p == "subdir"

        result = list_dir_flat(ws, "")
        assert "Directory:" in result
        assert "[FILE]" in result
        assert "[DIR]" in result
