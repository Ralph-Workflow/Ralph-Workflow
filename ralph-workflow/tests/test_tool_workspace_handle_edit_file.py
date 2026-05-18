"""Tests for ralph/mcp/tool_workspace.py — MCP workspace tool handlers."""

from __future__ import annotations
from tests.mock_session import MockSession

import json
from typing import cast
from unittest.mock import MagicMock

import pytest

from ralph.mcp.tools.coordination import (
    CapabilityDeniedError,
    InvalidParamsError,
    ToolContent,
)
from ralph.mcp.tools.workspace import (
    WORKSPACE_EDIT_CAPABILITY,
    handle_edit_file,
)

MEDIA_READ_CAPABILITY = "media.read"
DEFAULT_MAX_INLINE_BYTES = 5_242_880


class TestHandleEditFile:
    def test_edits_file_successfully(self) -> None:
        ws = MagicMock()
        ws.read.return_value = "hello world"
        ws.write.return_value = None

        result = handle_edit_file(
            MockSession(WORKSPACE_EDIT_CAPABILITY),
            ws,
            {"path": "file.txt", "edits": [{"oldText": "world", "newText": "there"}]},
        )
        assert result.is_error is False
        payload = json.loads(cast("ToolContent", result.content[0]).text)
        assert payload["status"] == "applied"
        ws.write.assert_called_once()

    def test_dry_run_does_not_write(self) -> None:
        ws = MagicMock()
        ws.read.return_value = "hello world"

        result = handle_edit_file(
            MockSession(WORKSPACE_EDIT_CAPABILITY),
            ws,
            {
                "path": "file.txt",
                "edits": [{"oldText": "world", "newText": "there"}],
                "dry_run": True,
            },
        )
        assert result.is_error is False
        payload = json.loads(cast("ToolContent", result.content[0]).text)
        assert payload["status"] == "preview"
        assert "diff" in payload
        ws.write.assert_not_called()

    def test_no_match_returns_error(self) -> None:
        ws = MagicMock()
        ws.read.return_value = "hello world"

        result = handle_edit_file(
            MockSession(WORKSPACE_EDIT_CAPABILITY),
            ws,
            {
                "path": "file.txt",
                "edits": [{"oldText": "not found", "newText": "replacement"}],
            },
        )
        assert result.is_error is True
        payload = json.loads(cast("ToolContent", result.content[0]).text)
        assert payload["status"] == "no_match"

    def test_multi_edit_applies_in_order(self) -> None:
        ws = MagicMock()
        ws.read.return_value = "a b c"
        ws.write.return_value = None

        result = handle_edit_file(
            MockSession(WORKSPACE_EDIT_CAPABILITY),
            ws,
            {
                "path": "file.txt",
                "edits": [
                    {"oldText": "a", "newText": "1"},
                    {"oldText": "b", "newText": "2"},
                ],
            },
        )
        assert result.is_error is False
        ws.write.assert_called_once()

    def test_missing_capability_raises(self) -> None:
        ws = MagicMock()
        ws.read.return_value = "content"

        with pytest.raises(CapabilityDeniedError):
            handle_edit_file(
                MockSession(),
                ws,
                {"path": "file.txt", "edits": [{"oldText": "content", "newText": "x"}]},
            )

    def test_empty_edits_raises(self) -> None:
        ws = MagicMock()

        with pytest.raises(InvalidParamsError):
            handle_edit_file(
                MockSession(WORKSPACE_EDIT_CAPABILITY), ws, {"path": "file.txt", "edits": []}
            )


