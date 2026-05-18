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
    WORKSPACE_EDIT_CAPABILITY,
    handle_move_file,
)

MEDIA_READ_CAPABILITY = "media.read"
DEFAULT_MAX_INLINE_BYTES = 5_242_880


class TestHandleMoveFile:
    def test_moves_file(self) -> None:
        ws = MagicMock()
        ws.move.return_value = None

        result = handle_move_file(
            MockSession(WORKSPACE_EDIT_CAPABILITY),
            ws,
            {"src": "old.txt", "dest": "new.txt"},
        )
        assert result.is_error is False
        payload = json.loads(cast("ToolContent", result.content[0]).text)
        assert payload["src"] == "old.txt"
        assert payload["dest"] == "new.txt"

    def test_overwrite_true_succeeds(self) -> None:
        ws = MagicMock()
        ws.move.return_value = None

        result = handle_move_file(
            MockSession(WORKSPACE_EDIT_CAPABILITY),
            ws,
            {"src": "old.txt", "dest": "new.txt", "overwrite": True},
        )
        assert result.is_error is False

    def test_missing_capability_raises(self) -> None:
        ws = MagicMock()

        with pytest.raises(CapabilityDeniedError):
            handle_move_file(MockSession(), ws, {"src": "a.txt", "dest": "b.txt"})


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


MockSession = TestHandleMoveFile.MockSession
