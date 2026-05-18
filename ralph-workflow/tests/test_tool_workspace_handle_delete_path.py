"""Tests for ralph/mcp/tool_workspace.py — MCP workspace tool handlers."""

from __future__ import annotations
from tests.mock_session import MockSession

import json
from typing import cast
from unittest.mock import MagicMock

import pytest

from ralph.mcp.tools.coordination import (
    CapabilityDeniedError,
    ToolContent,
)
from ralph.mcp.tools.workspace import (
    WORKSPACE_DELETE_CAPABILITY,
    WORKSPACE_EDIT_CAPABILITY,
    handle_delete_path,
)

MEDIA_READ_CAPABILITY = "media.read"
DEFAULT_MAX_INLINE_BYTES = 5_242_880


class TestHandleDeletePath:
    def test_deletes_file(self) -> None:
        ws = MagicMock()
        ws.delete.return_value = None

        result = handle_delete_path(
            MockSession(WORKSPACE_DELETE_CAPABILITY),
            ws,
            {"path": "file.txt"},
        )
        assert result.is_error is False
        payload = json.loads(cast("ToolContent", result.content[0]).text)
        assert payload["path"] == "file.txt"
        assert payload["deleted"] is True

    def test_deletes_directory_recursively(self) -> None:
        ws = MagicMock()
        ws.delete.return_value = None

        result = handle_delete_path(
            MockSession(WORKSPACE_DELETE_CAPABILITY),
            ws,
            {"path": "dir", "recursive": True},
        )
        assert result.is_error is False

    def test_refuses_directory_without_recursive(self) -> None:
        ws = MagicMock()
        ws.delete.side_effect = IsADirectoryError("Is a directory")

        result = handle_delete_path(
            MockSession(WORKSPACE_DELETE_CAPABILITY),
            ws,
            {"path": "dir"},
        )
        assert result.is_error is True

    def test_workspace_delete_distinct_from_edit(self) -> None:
        """WorkspaceDelete capability is distinct from WorkspaceEdit."""
        ws = MagicMock()
        ws.delete.return_value = None

        with pytest.raises(CapabilityDeniedError):
            handle_delete_path(MockSession(WORKSPACE_EDIT_CAPABILITY), ws, {"path": "file.txt"})

    def test_missing_capability_raises(self) -> None:
        ws = MagicMock()

        with pytest.raises(CapabilityDeniedError):
            handle_delete_path(MockSession(), ws, {"path": "file.txt"})


