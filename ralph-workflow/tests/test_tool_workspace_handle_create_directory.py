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
    handle_create_directory,
)

MEDIA_READ_CAPABILITY = "media.read"
DEFAULT_MAX_INLINE_BYTES = 5_242_880


class TestHandleCreateDirectory:
    def test_creates_directory(self) -> None:
        ws = MagicMock()
        ws.mkdirs.return_value = None

        result = handle_create_directory(
            MockSession(WORKSPACE_EDIT_CAPABILITY),
            ws,
            {"path": "new/dir"},
        )
        assert result.is_error is False
        payload = json.loads(cast("ToolContent", result.content[0]).text)
        assert payload["path"] == "new/dir"
        assert payload["created"] is True

    def test_missing_capability_raises(self) -> None:
        ws = MagicMock()

        with pytest.raises(CapabilityDeniedError):
            handle_create_directory(MockSession(), ws, {"path": "new/dir"})


