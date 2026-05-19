"""Tests for ralph/mcp/tools/git_read.py — MCP git read tool handlers."""

from __future__ import annotations

import pytest

from ralph.mcp.tools.coordination import InvalidParamsError
from ralph.mcp.tools.git_read import (
    parse_git_show_params,
)

CUSTOM_LOG_COUNT = 20

# =============================================================================
# Mock infrastructure
# =============================================================================


class TestParseGitShowParams:
    def test_parses_ref(self) -> None:
        params = {"ref": "HEAD~1"}
        result = parse_git_show_params(params)
        assert result.git_ref == "HEAD~1"

    def test_missing_ref_raises(self) -> None:
        params: dict[str, object] = {}
        with pytest.raises(InvalidParamsError):
            parse_git_show_params(params)

    def test_non_string_ref_raises(self) -> None:
        params = {"ref": 123}
        with pytest.raises(InvalidParamsError):
            parse_git_show_params(params)
