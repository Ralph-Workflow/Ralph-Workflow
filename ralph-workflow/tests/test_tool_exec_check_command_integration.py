"""Tests for ralph/mcp/tool_exec.py — MCP exec tool handler."""

from __future__ import annotations

from ralph.mcp.tools.exec import check_command

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
        reason = check_command("sudo", ["ls"])
        assert reason is not None

    def test_pip_install_is_allowed(self) -> None:
        assert check_command("pip", ["install", "requests"]) is None

    def test_pip_install_user_is_allowed(self) -> None:
        assert check_command("pip", ["install", "--user", "requests"]) is None

    def test_npm_install_global_is_allowed(self) -> None:
        assert check_command("npm", ["install", "-g", "lodash"]) is None

    def test_cargo_install_is_allowed(self) -> None:
        assert check_command("cargo", ["install", "ripgrep"]) is None

    def test_apt_install_is_allowed(self) -> None:
        assert check_command("apt", ["install", "vim"]) is None

    def test_brew_install_is_allowed(self) -> None:
        assert check_command("brew", ["install", "ripgrep"]) is None
