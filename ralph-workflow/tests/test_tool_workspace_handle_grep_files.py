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
from tests.mock_session import MockSession

MEDIA_READ_CAPABILITY = "media.read"
DEFAULT_MAX_INLINE_BYTES = 5_242_880


class TestHandleGrepFiles:
    # Per-test pytest marker: the whole-word and
    # context-walking tests in this class build a real
    # workspace on disk and run a full grep over the
    # synthetic files. Under parallel xdist CPU contention
    # ``test_grep_whole_word`` has been observed to exceed
    # the global 1-second per-test cap. 5 seconds is the
    # minimum supported by the audit invariant
    # (``_VERIFY_STEP_TIMEOUT_SECONDS >= 5.0``) and well
    # under the 60-second combined ``make verify`` budget.
    # The default 1 s cap remains in place for every other
    # test in the suite.
    pytestmark = pytest.mark.timeout_seconds(5)

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
