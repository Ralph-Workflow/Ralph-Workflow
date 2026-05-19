"""Tests for ralph/mcp/tool_exec.py — MCP exec tool handler."""

from __future__ import annotations

from ralph.mcp.tools.exec import (
    check_version_control,
)

CUSTOM_TIMEOUT_MS = 5000
EXPECTED_TIMEOUT_SECONDS = 2.5


class TestCheckVersionControl:
    def test_git_is_blacklisted(self) -> None:
        reason = check_version_control("git", ["status"])
        assert reason is not None
        assert "git" in reason.lower()

    def test_svn_is_blacklisted(self) -> None:
        reason = check_version_control("svn", ["update"])
        assert reason is not None

    def test_allowed_command_returns_none(self) -> None:
        reason = check_version_control("ls", [])
        assert reason is None

    def test_git_uppercase_is_blacklisted(self) -> None:
        reason = check_version_control("GIT", ["status"])
        assert reason is not None
