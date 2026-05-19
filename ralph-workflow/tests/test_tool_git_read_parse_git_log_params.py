"""Tests for ralph/mcp/tools/git_read.py — MCP git read tool handlers."""

from __future__ import annotations

from ralph.mcp.tools.git_read import (
    DEFAULT_LOG_COUNT,
    parse_git_log_params,
)

CUSTOM_LOG_COUNT = 20

# =============================================================================
# Mock infrastructure
# =============================================================================


class TestParseGitLogParams:
    def test_parses_count(self) -> None:
        params = {"count": CUSTOM_LOG_COUNT}
        result = parse_git_log_params(params)
        assert result.count == CUSTOM_LOG_COUNT

    def test_defaults_to_10(self) -> None:
        params: dict[str, object] = {}
        result = parse_git_log_params(params)
        assert result.count == DEFAULT_LOG_COUNT

    def test_negative_count_defaults_to_10(self) -> None:
        params = {"count": -5}
        result = parse_git_log_params(params)
        assert result.count == DEFAULT_LOG_COUNT

    def test_non_int_count_defaults_to_10(self) -> None:
        params: dict[str, object] = {"count": "many"}
        result = parse_git_log_params(params)
        assert result.count == DEFAULT_LOG_COUNT
