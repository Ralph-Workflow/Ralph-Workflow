"""Tests for ralph/mcp/tool_exec.py — MCP exec tool handler."""

from __future__ import annotations

from ralph.mcp.tools.exec import (
    check_command,
)

CUSTOM_TIMEOUT_MS = 5000
EXPECTED_TIMEOUT_SECONDS = 2.5


class TestCheckCommandIntegration:
    def test_empty_command_is_allowed(self) -> None:
        reason = check_command("", [])
        assert reason is None

    def test_whitespace_command_is_allowed(self) -> None:
        reason = check_command("   ", [])
        assert reason is None

    def test_all_blacklist_checks_applied(self) -> None:
        # Test that all individual check functions are called
        # by checking one that would fail the first check
        reason = check_command("sudo", ["ls"])
        assert reason is not None
