"""Tests for ralph/mcp/tool_workspace.py — MCP workspace tool handlers."""

from __future__ import annotations

import json
from typing import cast
from unittest.mock import MagicMock

import pytest

from ralph.mcp.tools.coordination import (
    CapabilityDeniedError,
    ToolContent,
)
from ralph.mcp.tools.workspace import (
    WORKSPACE_METADATA_READ_CAPABILITY,
    handle_stat,
)
from tests.mock_session import MockSession

MEDIA_READ_CAPABILITY = "media.read"
DEFAULT_MAX_INLINE_BYTES = 5_242_880


class TestHandleStat:
    def test_stat_returns_metadata(self) -> None:
        ws = MagicMock()
        ws.stat.return_value = {
            "type": "file",
            "size_bytes": 100,
            "created_unix": 123456.0,
            "modified_unix": 789012.0,
            "mode": 33188,
        }

        result = handle_stat(
            MockSession(WORKSPACE_METADATA_READ_CAPABILITY), ws, {"path": "file.txt"}
        )
        assert result.is_error is False
        payload = json.loads(cast("ToolContent", result.content[0]).text)
        assert payload["type"] == "file"
        assert payload["size_bytes"] == 100

    def test_stat_missing_file(self) -> None:
        ws = MagicMock()
        ws.stat.return_value = {"type": "missing"}

        result = handle_stat(
            MockSession(WORKSPACE_METADATA_READ_CAPABILITY),
            ws,
            {"path": "missing.txt"},
        )
        assert result.is_error is False
        payload = json.loads(cast("ToolContent", result.content[0]).text)
        assert payload["type"] == "missing"

    def test_missing_capability_raises(self) -> None:
        ws = MagicMock()

        with pytest.raises(CapabilityDeniedError):
            handle_stat(MockSession(), ws, {"path": "file.txt"})
