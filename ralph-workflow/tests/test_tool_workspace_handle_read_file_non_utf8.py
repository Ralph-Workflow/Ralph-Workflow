"""Tests for ralph/mcp/tool_workspace.py — MCP workspace tool handlers."""

from __future__ import annotations
from tests.mock_session import MockSession

import json
from typing import cast
from unittest.mock import MagicMock

import pytest

from ralph.mcp.tools.coordination import (
    ToolContent,
    ToolError,
)
from ralph.mcp.tools.workspace import (
    WORKSPACE_READ_CAPABILITY,
    handle_read_file,
)

MEDIA_READ_CAPABILITY = "media.read"
DEFAULT_MAX_INLINE_BYTES = 5_242_880


class TestHandleReadFileNonUtf8:
    """Tests for structured non-UTF-8 error in handle_read_file."""

    def test_returns_structured_error_for_unicode_decode_error(self) -> None:
        ws = MagicMock()
        ws.stat.return_value = {"type": "file", "size_bytes": 100}
        ws.read.side_effect = UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")

        result = handle_read_file(
            MockSession(WORKSPACE_READ_CAPABILITY), ws, {"path": "binary.bin"}
        )
        assert result.is_error is True
        payload = json.loads(cast("ToolContent", result.content[0]).text)
        assert payload["status"] == "binary_or_invalid_utf8"
        assert payload["path"] == "binary.bin"
        assert payload["byte_offset"] == 0

    def test_propagates_other_exceptions_as_tool_error(self) -> None:
        ws = MagicMock()
        ws.stat.return_value = {"type": "file", "size_bytes": 100}
        ws.read.side_effect = RuntimeError("unexpected disk error")

        with pytest.raises(ToolError):
            handle_read_file(MockSession(WORKSPACE_READ_CAPABILITY), ws, {"path": "file.txt"})


