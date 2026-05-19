"""Tests for ralph/mcp/tool_exec.py — MCP exec tool handler."""

from __future__ import annotations

from unittest.mock import MagicMock

from ralph.mcp.tools.exec import (
    format_exec_result,
)

CUSTOM_TIMEOUT_MS = 5000
EXPECTED_TIMEOUT_SECONDS = 2.5


class TestFormatExecResult:
    def test_format_includes_command_and_exit_code(self) -> None:
        process = MagicMock()
        process.stdout = b"output"
        process.stderr = b""
        process.returncode = 0
        result = format_exec_result("echo", ["test"], process, 5000)
        assert "echo" in result
        assert "test" in result
        assert "0" in result

    def test_format_includes_stdout_and_stderr(self) -> None:
        process = MagicMock()
        process.stdout = b"hello"
        process.stderr = b"error"
        process.returncode = 1
        result = format_exec_result("cmd", [], process, 5000)
        assert "hello" in result
        assert "error" in result
        assert "1" in result

    def test_format_adds_timeout_note_when_under_threshold(self) -> None:
        process = MagicMock()
        process.stdout = b""
        process.stderr = b""
        process.returncode = 0
        result = format_exec_result("cmd", [], process, 45000)
        assert "timeout" in result.lower()
