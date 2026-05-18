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
    handle_grep_files,
)

MEDIA_READ_CAPABILITY = "media.read"
DEFAULT_MAX_INLINE_BYTES = 5_242_880


class TestHandleGrepFiles:
    def test_grep_finds_matches(self) -> None:
        ws = MagicMock()
        ws.iter_files.return_value = ("file.py",)
        ws.stat.return_value = {"type": "file", "size_bytes": 100}
        ws.read.return_value = "def foo():\n    pass\n"

        result = handle_grep_files(
            MockSession(WORKSPACE_READ_CAPABILITY),
            ws,
            {"pattern": "def foo", "path": "."},
        )
        assert result.is_error is False
        payload = json.loads(cast("ToolContent", result.content[0]).text)
        assert len(payload["matches"]) > 0
        assert payload["matches"][0]["text"] == "def foo():"

    def test_grep_literal_mode(self) -> None:
        ws = MagicMock()
        ws.iter_files.return_value = ("file.py",)
        ws.stat.return_value = {"type": "file", "size_bytes": 100}
        ws.read.return_value = "def foo():\n    pass\n"

        result = handle_grep_files(
            MockSession(WORKSPACE_READ_CAPABILITY),
            ws,
            {"pattern": "def foo", "path": ".", "regex": False},
        )
        assert result.is_error is False

    def test_grep_case_insensitive(self) -> None:
        ws = MagicMock()
        ws.iter_files.return_value = ("file.py",)
        ws.stat.return_value = {"type": "file", "size_bytes": 100}
        ws.read.return_value = "Def Foo():\n    pass\n"

        result = handle_grep_files(
            MockSession(WORKSPACE_READ_CAPABILITY),
            ws,
            {"pattern": "def foo", "path": ".", "case_sensitive": False},
        )
        assert result.is_error is False
        payload = json.loads(cast("ToolContent", result.content[0]).text)
        assert len(payload["matches"]) > 0

    def test_grep_whole_word(self) -> None:
        ws = MagicMock()
        ws.iter_files.return_value = ("file.py",)
        ws.stat.return_value = {"type": "file", "size_bytes": 100}
        ws.read.return_value = "def foo():\n    pass\n"

        result = handle_grep_files(
            MockSession(WORKSPACE_READ_CAPABILITY),
            ws,
            {"pattern": "foo", "path": ".", "whole_word": True},
        )
        assert result.is_error is False

    def test_grep_with_context(self) -> None:
        ws = MagicMock()
        ws.iter_files.return_value = ("file.py",)
        ws.stat.return_value = {"type": "file", "size_bytes": 100}
        ws.read.return_value = "line0\nline1\nline2\nline3\n"

        result = handle_grep_files(
            MockSession(WORKSPACE_READ_CAPABILITY),
            ws,
            {"pattern": "line2", "path": ".", "context_before": 1, "context_after": 1},
        )
        assert result.is_error is False
        payload = json.loads(cast("ToolContent", result.content[0]).text)
        assert len(payload["matches"]) > 0
        match = payload["matches"][0]
        assert "context_before" in match
        assert "context_after" in match

    def test_grep_invalid_regex_raises(self) -> None:
        ws = MagicMock()

        with pytest.raises(InvalidParamsError):
            handle_grep_files(
                MockSession(WORKSPACE_READ_CAPABILITY),
                ws,
                {"pattern": "[invalid", "path": "."},
            )

    def test_grep_missing_capability_raises(self) -> None:
        ws = MagicMock()

        with pytest.raises(CapabilityDeniedError):
            handle_grep_files(MockSession(), ws, {"pattern": "foo", "path": "."})


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


MockSession = TestHandleGrepFiles.MockSession
