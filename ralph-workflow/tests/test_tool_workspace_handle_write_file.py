"""Tests for ralph/mcp/tool_workspace.py — MCP workspace tool handlers."""

from __future__ import annotations

from typing import cast
from unittest.mock import MagicMock

import pytest

from ralph.mcp.tools.coordination import (
    InvalidParamsError,
    ToolContent,
)
from ralph.mcp.tools.workspace import (
    WORKSPACE_WRITE_EPHEMERAL_CAPABILITY,
    WORKSPACE_WRITE_TRACKED_CAPABILITY,
    handle_write_file,
)
from tests.mock_session import MockSession

MEDIA_READ_CAPABILITY = "media.read"
DEFAULT_MAX_INLINE_BYTES = 5_242_880


class TestHandleWriteFile:
    def test_writes_new_file_as_ephemeral(self) -> None:
        ws = MagicMock()
        ws.exists.return_value = False
        ws.write.return_value = None

        result = handle_write_file(
            MockSession(WORKSPACE_WRITE_EPHEMERAL_CAPABILITY),
            ws,
            {"path": "new.txt", "content": "hello"},
        )
        assert result.is_error is False
        assert "new.txt" in cast("ToolContent", result.content[0]).text
        ws.write.assert_called_once()

    def test_writes_git_tracked_file_with_tracked_capability(self) -> None:
        ws = MagicMock()
        ws.exists.return_value = True
        ws.write.return_value = None

        session = MockSession(WORKSPACE_WRITE_TRACKED_CAPABILITY)
        result = handle_write_file(session, ws, {"path": "src/main.py", "content": "code"})
        assert result.is_error is False

    def test_missing_path_raises(self) -> None:
        ws = MagicMock()

        with pytest.raises(InvalidParamsError):
            handle_write_file(
                MockSession(WORKSPACE_WRITE_EPHEMERAL_CAPABILITY),
                ws,
                {"content": "hello"},
            )

    def test_missing_content_raises(self) -> None:
        ws = MagicMock()
        ws.exists.return_value = False  # file is untracked/ephemeral

        with pytest.raises(InvalidParamsError):
            handle_write_file(
                MockSession(WORKSPACE_WRITE_EPHEMERAL_CAPABILITY),
                ws,
                {"path": "file.txt"},
            )
