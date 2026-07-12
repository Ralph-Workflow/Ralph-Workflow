"""Tests for ralph/mcp/tool_exec.py — MCP exec tool handler."""

from __future__ import annotations

import pytest

from ralph.mcp.tools.exec import (
    _shell_command_segments,
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


class TestShellCommandSegments:
    """The pipeline splitter feeds the per-segment blacklist, so every command
    the shell would run must surface as its own ``(command, args)`` segment."""

    def test_pipe_splits_into_command_heads(self) -> None:
        segments = _shell_command_segments("grep -r foo . | wc -l")
        assert segments == [("grep", ["-r", "foo", "."]), ("wc", ["-l"])]

    @pytest.mark.parametrize("separator", ["|", ";", "&&", "||", "&"])
    def test_command_after_separator_is_a_segment_head(self, separator: str) -> None:
        segments = _shell_command_segments(f"echo hi {separator} sudo whoami")
        heads = [command for command, _ in segments]
        assert "sudo" in heads

    def test_redirection_target_is_not_a_command_head(self) -> None:
        # The token after ``>`` is a filename, so a file named like a
        # blacklisted command must NOT be misread as invoking it.
        segments = _shell_command_segments("echo x > reboot")
        assert segments == [("echo", ["x"])]

    def test_redirection_then_pipe_keeps_next_command_head(self) -> None:
        segments = _shell_command_segments("echo hi > out.txt | grep hi")
        heads = [command for command, _ in segments]
        assert heads == ["echo", "grep"]
