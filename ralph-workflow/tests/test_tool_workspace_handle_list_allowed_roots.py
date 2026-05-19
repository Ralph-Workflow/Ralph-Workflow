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
    handle_list_allowed_roots,
)
from tests.mock_session import MockSession

MEDIA_READ_CAPABILITY = "media.read"
DEFAULT_MAX_INLINE_BYTES = 5_242_880


class TestHandleListAllowedRoots:
    def test_returns_allowed_roots(self) -> None:
        ws = MagicMock()
        ws.allowed_roots.return_value = ["/workspace", "/project"]

        result = handle_list_allowed_roots(MockSession(WORKSPACE_READ_CAPABILITY), ws, {})
        assert result.is_error is False
        payload = json.loads(cast("ToolContent", result.content[0]).text)
        assert payload["allowed_roots"] == ["/workspace", "/project"]

    def test_missing_capability_raises(self) -> None:
        ws = MagicMock()

        with pytest.raises(CapabilityDeniedError):
            handle_list_allowed_roots(MockSession(), ws, {})
