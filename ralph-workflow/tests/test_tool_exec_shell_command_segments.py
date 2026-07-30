"""Tests for the shell pipeline splitter behind the MCP exec blacklist."""

from __future__ import annotations

import pytest

from ralph.mcp.tools.exec import _shell_command_segments


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
