"""Tests for ralph/mcp/tool_workspace.py — MCP workspace tool handlers."""

from __future__ import annotations

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
    WORKSPACE_READ_CAPABILITY,
    handle_read_multiple_files,
)

MEDIA_READ_CAPABILITY = "media.read"
DEFAULT_MAX_INLINE_BYTES = 5_242_880


class TestHandleReadMultipleFiles:
    def test_reads_multiple_files(self) -> None:
        ws = MagicMock()
        ws.read.side_effect = ["content1", "content2"]

        result = handle_read_multiple_files(
            MockSession(WORKSPACE_READ_CAPABILITY),
            ws,
            {"paths": ["file1.txt", "file2.txt"]},
        )
        assert result.is_error is False
        payload = json.loads(cast("ToolContent", result.content[0]).text)
        assert len(payload["files"]) == 2
        assert payload["files"][0]["content"] == "content1"
        assert payload["files"][1]["content"] == "content2"

    def test_partial_failure_returns_error_per_file(self) -> None:
        ws = MagicMock()
        ws.read.side_effect = ["content1", FileNotFoundError("not found")]

        result = handle_read_multiple_files(
            MockSession(WORKSPACE_READ_CAPABILITY),
            ws,
            {"paths": ["file1.txt", "missing.txt"]},
        )
        assert result.is_error is False
        payload = json.loads(cast("ToolContent", result.content[0]).text)
        assert payload["files"][0]["content"] == "content1"
        assert "error" in payload["files"][1]

    def test_missing_capability_raises(self) -> None:
        ws = MagicMock()

        with pytest.raises(CapabilityDeniedError):
            handle_read_multiple_files(MockSession(), ws, {"paths": ["file.txt"]})

    def test_non_list_paths_raises(self) -> None:
        ws = MagicMock()

        with pytest.raises(InvalidParamsError):
            handle_read_multiple_files(
                MockSession(WORKSPACE_READ_CAPABILITY), ws, {"paths": "not a list"}
            )


    class MockSession:
        session_id = "test-session"

        def __init__(self, *args: object) -> None:
            if not args:
                self._caps: set[str] = set()
            elif len(args) == 1 and isinstance(args[0], set):
                self._caps = {s for s in args[0] if isinstance(s, str)}
            else:
                self._caps = {s for s in args if isinstance(s, str)}

        def check_capability(self, capability: str) -> object:
            return capability in self._caps


MockSession = TestHandleReadMultipleFiles.MockSession
