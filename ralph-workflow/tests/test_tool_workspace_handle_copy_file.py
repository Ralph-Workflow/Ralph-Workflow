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
    WORKSPACE_EDIT_CAPABILITY,
    handle_copy_file,
)

MEDIA_READ_CAPABILITY = "media.read"
DEFAULT_MAX_INLINE_BYTES = 5_242_880


class TestHandleCopyFile:
    def test_copies_file(self) -> None:
        ws = MagicMock()
        ws.copy.return_value = None

        result = handle_copy_file(
            MockSession(WORKSPACE_EDIT_CAPABILITY),
            ws,
            {"src": "original.txt", "dest": "copy.txt"},
        )
        assert result.is_error is False
        payload = json.loads(cast("ToolContent", result.content[0]).text)
        assert payload["src"] == "original.txt"
        assert payload["dest"] == "copy.txt"

    def test_missing_capability_raises(self) -> None:
        ws = MagicMock()

        with pytest.raises(CapabilityDeniedError):
            handle_copy_file(MockSession(), ws, {"src": "a.txt", "dest": "b.txt"})


