"""Tests for ralph/mcp/tool_workspace.py — MCP workspace tool handlers."""

from __future__ import annotations

import pytest

from ralph.mcp.tools.coordination import (
    InvalidParamsError,
)
from ralph.mcp.tools.workspace import (
    required_string_param,
)

MEDIA_READ_CAPABILITY = "media.read"
DEFAULT_MAX_INLINE_BYTES = 5_242_880


class TestRequiredStringParam:
    def test_returns_string_value(self) -> None:
        params: dict[str, object] = {"path": "/some/path"}
        result = required_string_param(params, "path")
        assert result == "/some/path"

    def test_missing_param_raises(self) -> None:
        params: dict[str, object] = {}
        with pytest.raises(InvalidParamsError):
            required_string_param(params, "path")

    def test_non_string_param_raises(self) -> None:
        params: dict[str, object] = {"path": 123}
        with pytest.raises(InvalidParamsError):
            required_string_param(params, "path")
