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
    WORKSPACE_READ_CAPABILITY,
    handle_directory_tree,
)

MEDIA_READ_CAPABILITY = "media.read"
DEFAULT_MAX_INLINE_BYTES = 5_242_880


class TestHandleDirectoryTree:
    def test_returns_json_tree(self) -> None:
        ws = MagicMock()

        def list_dir_effect(p: str) -> list[str]:
            if p in (".", ""):
                return ["file.txt", "subdir"]
            return []

        # Handle both normalized ("") and non-normalized (".") path forms
        ws.is_dir.side_effect = lambda p: p in (".", "")
        ws.list_dir.side_effect = list_dir_effect
        ws.is_file.side_effect = lambda p: p == "file.txt"
        ws.exists.side_effect = lambda p: False

        result = handle_directory_tree(
            MockSession(WORKSPACE_READ_CAPABILITY),
            ws,
            {"path": "."},
        )
        assert result.is_error is False
        payload = json.loads(cast("ToolContent", result.content[0]).text)
        assert payload["type"] == "dir"
        assert "children" in payload
        assert len(payload["children"]) == 2

    def test_respects_max_depth(self) -> None:
        ws = MagicMock()
        ws.is_dir.side_effect = lambda p: p in (".", "")
        ws.list_dir.side_effect = lambda p: ["subdir"] if p in (".", "") else []
        ws.is_file.side_effect = lambda p: p == "subdir"
        ws.exists.side_effect = lambda p: False

        result = handle_directory_tree(
            MockSession(WORKSPACE_READ_CAPABILITY),
            ws,
            {"path": ".", "max_depth": 1},
        )
        assert result.is_error is False
        payload = json.loads(cast("ToolContent", result.content[0]).text)
        assert len(payload["children"]) > 0
        for child in payload["children"]:
            if child["type"] == "dir":
                assert child["children"] == []

    def test_missing_capability_raises(self) -> None:
        ws = MagicMock()

        with pytest.raises(CapabilityDeniedError):
            handle_directory_tree(MockSession(), ws, {"path": "."})


