"""Tests for ralph/mcp/tools/git_read.py — MCP git read tool handlers."""

from __future__ import annotations

from pathlib import Path

from ralph.mcp.tools.git_read import (
    parse_git_diff_params,
)

CUSTOM_LOG_COUNT = 20

# =============================================================================
# Mock infrastructure
# =============================================================================


class TestParseGitDiffParams:

    class MockSession:
        """Mock session for capability checks."""

        def __init__(self, capabilities: set[str]) -> None:
            self.session_id = "test-session"
            self._capabilities = capabilities

        def check_capability(self, capability: str) -> object:
            return capability in self._capabilities

    class MockWorkspaceRoot:
        """Mock workspace with root property."""

        def __init__(self, root: Path | str) -> None:
            self.root = Path(root) if isinstance(root, str) else root

    def test_parses_string_args(self) -> None:
        params = {"args": ["--staged", "--name-only"]}
        result = parse_git_diff_params(params)
        assert result.args == ["--staged", "--name-only"]

    def test_filters_non_string_args(self) -> None:
        params = {"args": ["--staged", 123, None, True, "--name-only"]}
        result = parse_git_diff_params(params)
        assert result.args == ["--staged", "--name-only"]

    def test_empty_args_for_non_list(self) -> None:
        params = {"args": "not a list"}
        result = parse_git_diff_params(params)
        assert result.args == []

    def test_missing_args_returns_empty_list(self) -> None:
        params: dict[str, object] = {}
        result = parse_git_diff_params(params)
        assert result.args == []


MockSession = TestParseGitDiffParams.MockSession
MockWorkspaceRoot = TestParseGitDiffParams.MockWorkspaceRoot
