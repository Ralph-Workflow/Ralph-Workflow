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
    WORKSPACE_READ_CAPABILITY,
    handle_search_files,
)
from tests.mock_session import MockSession

MEDIA_READ_CAPABILITY = "media.read"
DEFAULT_MAX_INLINE_BYTES = 5_242_880


class TestHandleSearchFiles:
    def test_search_finds_matching_files(self) -> None:
        ws = MagicMock()
        ws.iter_files.return_value = ("main.py", "test.py")
        ws.is_dir.return_value = False
        ws.is_file.return_value = True

        result = handle_search_files(
            MockSession(WORKSPACE_READ_CAPABILITY),
            ws,
            {"pattern": "*.py", "path": "."},
        )
        assert result.is_error is False
        payload = json.loads(cast("ToolContent", result.content[0]).text)
        assert "main.py" in payload["matches"]
        assert "test.py" in payload["matches"]

    def test_search_with_exclude(self) -> None:
        ws = MagicMock()
        ws.iter_files.return_value = ("file.py", "test_file.py")

        result = handle_search_files(
            MockSession(WORKSPACE_READ_CAPABILITY),
            ws,
            {"pattern": "*.py", "path": ".", "exclude": ["test_*.py"]},
        )
        assert result.is_error is False
        payload = json.loads(cast("ToolContent", result.content[0]).text)
        assert "file.py" in payload["matches"]
        assert "test_file.py" not in payload["matches"]

    def test_search_with_limit(self) -> None:
        ws = MagicMock()
        ws.iter_files.return_value = ("file1.py", "file2.py", "file3.py")

        result = handle_search_files(
            MockSession(WORKSPACE_READ_CAPABILITY),
            ws,
            {"pattern": "*.py", "path": ".", "limit": 2},
        )
        assert result.is_error is False
        payload = json.loads(cast("ToolContent", result.content[0]).text)
        assert payload["truncated"] is True
        assert len(payload["matches"]) == 2

    def test_search_missing_capability_raises(self) -> None:
        ws = MagicMock()

        with pytest.raises(CapabilityDeniedError):
            handle_search_files(MockSession(), ws, {"pattern": "*", "path": "."})
