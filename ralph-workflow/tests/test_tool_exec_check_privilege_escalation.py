"""Tests for ralph/mcp/tool_exec.py — MCP exec tool handler."""

from __future__ import annotations

from ralph.mcp.tools.exec import (
    check_privilege_escalation,
)

CUSTOM_TIMEOUT_MS = 5000
EXPECTED_TIMEOUT_SECONDS = 2.5


class TestCheckPrivilegeEscalation:
    def test_sudo_is_blacklisted(self) -> None:
        reason = check_privilege_escalation("sudo", ["ls"])
        assert reason is not None
        assert "sudo" in reason.lower()

    def test_su_is_blacklisted(self) -> None:
        reason = check_privilege_escalation("su", ["-"])
        assert reason is not None

    def test_allowed_command_returns_none(self) -> None:
        reason = check_privilege_escalation("cat", [])
        assert reason is None
