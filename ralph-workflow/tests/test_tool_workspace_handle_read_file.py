"""Tests for ralph/mcp/tool_workspace.py — MCP workspace tool handlers."""

from __future__ import annotations

from typing import cast
from unittest.mock import MagicMock

import pytest

from ralph.mcp.tools.coordination import (
    CapabilityDeniedError,
    InvalidParamsError,
    ToolContent,
    ToolError,
)
from ralph.mcp.tools.workspace import (
    WORKSPACE_READ_CAPABILITY,
    handle_read_file,
)
from tests.mock_session import MockSession

MEDIA_READ_CAPABILITY = "media.read"
DEFAULT_MAX_INLINE_BYTES = 5_242_880


class TestHandleReadFile:
    def test_reads_file_content(self) -> None:
        ws = MagicMock()
        ws.read.return_value = "file contents"

        result = handle_read_file(MockSession(WORKSPACE_READ_CAPABILITY), ws, {"path": "file.txt"})
        assert "file contents" in cast("ToolContent", result.content[0]).text
        assert result.is_error is False

    def test_missing_capability_raises(self) -> None:
        ws = MagicMock()

        with pytest.raises(CapabilityDeniedError):
            handle_read_file(MockSession(), ws, {"path": "file.txt"})

    def test_missing_path_raises(self) -> None:
        ws = MagicMock()

        with pytest.raises(InvalidParamsError):
            handle_read_file(MockSession(WORKSPACE_READ_CAPABILITY), ws, {})

    def test_file_not_found_raises_tool_error(self) -> None:
        ws = MagicMock()
        ws.read.side_effect = FileNotFoundError("not found")

        with pytest.raises(ToolError):
            handle_read_file(MockSession(WORKSPACE_READ_CAPABILITY), ws, {"path": "missing.txt"})
