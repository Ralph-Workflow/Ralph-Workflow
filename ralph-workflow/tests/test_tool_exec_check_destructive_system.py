"""Tests for ralph/mcp/tool_exec.py — MCP exec tool handler."""

from __future__ import annotations

from ralph.mcp.tools.exec import (
    check_destructive_system,
)

CUSTOM_TIMEOUT_MS = 5000
EXPECTED_TIMEOUT_SECONDS = 2.5


class TestCheckDestructiveSystem:
    def test_shutdown_is_blacklisted(self) -> None:
        reason = check_destructive_system("shutdown", ["-h", "now"])
        assert reason is not None
        assert "shutdown" in reason.lower()

    def test_reboot_is_blacklisted(self) -> None:
        reason = check_destructive_system("reboot", [])
        assert reason is not None

    def test_rm_rf_root_is_blacklisted(self) -> None:
        reason = check_destructive_system("rm", ["-rf", "/"])
        assert reason is not None
        assert "rm" in reason.lower()

    def test_rm_rf_home_is_blacklisted(self) -> None:
        reason = check_destructive_system("rm", ["-rf", "/home/user"])
        assert reason is not None

    def test_rm_rf_dotfile_is_blacklisted(self) -> None:
        reason = check_destructive_system("rm", ["-rf", "~/.bashrc"])
        assert reason is not None

    def test_rm_without_flags_is_allowed(self) -> None:
        reason = check_destructive_system("rm", ["file.txt"])
        assert reason is None

    def test_mkfs_with_dev_target_is_blacklisted(self) -> None:
        reason = check_destructive_system("mkfs", ["/dev/sda1"])
        assert reason is not None

    def test_dd_with_dev_output_is_blacklisted(self) -> None:
        reason = check_destructive_system("dd", ["if=/dev/zero", "of=/dev/sda"])
        assert reason is not None

    def test_kill_minus_9_1_is_blacklisted(self) -> None:
        reason = check_destructive_system("kill", ["-9", "1"])
        assert reason is not None
        assert "kill" in reason.lower()
