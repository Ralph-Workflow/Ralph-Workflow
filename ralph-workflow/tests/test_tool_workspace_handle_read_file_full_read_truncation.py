"""Tests for ralph/mcp/tool_workspace.py — MCP workspace tool handlers."""

from __future__ import annotations

import json
from typing import cast
from unittest.mock import MagicMock

import pytest

from ralph.mcp.tools.coordination import (
    ToolContent,
    ToolError,
)
from ralph.mcp.tools.workspace import (
    FULL_READ_DEFAULT_MAX_BYTES,
    WORKSPACE_READ_CAPABILITY,
    handle_read_file,
)
from tests.mock_session import MockSession

MEDIA_READ_CAPABILITY = "media.read"
DEFAULT_MAX_INLINE_BYTES = 5_242_880


class TestHandleReadFileFullReadTruncation:
    """Tests for oversize truncation in handle_read_file."""

    def test_small_file_returns_plain_text(self) -> None:
        ws = MagicMock()
        ws.stat.return_value = {"type": "file", "size_bytes": 100}
        ws.read.return_value = "small content"

        result = handle_read_file(MockSession(WORKSPACE_READ_CAPABILITY), ws, {"path": "small.txt"})
        assert result.is_error is False
        assert cast("ToolContent", result.content[0]).text == "small content"

    def test_oversize_file_returns_truncation_envelope(self) -> None:
        ws = MagicMock()
        ws.stat.return_value = {"type": "file", "size_bytes": 10_000_000}
        ws.read_lines.return_value = (
            "first 5MB worth",
            {"total_lines": 50000, "returned_lines": 19531, "truncated": True},
        )

        result = handle_read_file(MockSession(WORKSPACE_READ_CAPABILITY), ws, {"path": "large.txt"})
        assert result.is_error is False
        payload = json.loads(cast("ToolContent", result.content[0]).text)
        assert payload["truncated"] is True
        assert payload["total_bytes"] == 10_000_000
        assert payload["max_bytes"] == FULL_READ_DEFAULT_MAX_BYTES
        assert payload["reason"] == "oversize"
        assert "content" in payload

    def test_explicit_max_bytes_override_respected(self) -> None:
        ws = MagicMock()
        ws.stat.return_value = {"type": "file", "size_bytes": 2000}
        ws.read_lines.return_value = (
            "truncated content",
            {"total_lines": 10, "returned_lines": 3, "truncated": True},
        )

        result = handle_read_file(
            MockSession(WORKSPACE_READ_CAPABILITY),
            ws,
            {"path": "file.txt", "max_bytes": 1000},
        )
        assert result.is_error is False
        payload = json.loads(cast("ToolContent", result.content[0]).text)
        assert payload["max_bytes"] == 1000
        assert payload["truncated"] is True

    def test_dir_path_falls_through_to_workspace_read(self) -> None:
        ws = MagicMock()
        ws.stat.return_value = {"type": "dir"}
        ws.read.side_effect = IsADirectoryError("is a directory")

        with pytest.raises(ToolError):
            handle_read_file(MockSession(WORKSPACE_READ_CAPABILITY), ws, {"path": "somedir"})
