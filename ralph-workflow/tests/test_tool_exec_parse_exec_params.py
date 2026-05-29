"""Tests for ralph/mcp/tool_exec.py — MCP exec tool handler."""

from __future__ import annotations

import pytest

from ralph.mcp.tools.coordination import (
    InvalidParamsError,
)
from ralph.mcp.tools.exec import (
    DEFAULT_TIMEOUT_MS,
    parse_exec_params,
)

CUSTOM_TIMEOUT_MS = 5000
EXPECTED_TIMEOUT_SECONDS = 2.5


class TestParseExecParams:
    def test_parses_valid_params(self) -> None:
        params = {"command": "ls", "args": ["-la"], "timeout_ms": CUSTOM_TIMEOUT_MS}
        result = parse_exec_params(params)
        assert result.command == "ls"
        assert result.args == ["-la"]
        assert result.timeout_ms == CUSTOM_TIMEOUT_MS

    def test_defaults_timeout(self) -> None:
        params = {"command": "ls", "args": []}
        result = parse_exec_params(params)
        assert result.timeout_ms == DEFAULT_TIMEOUT_MS

    def test_ignores_non_string_args(self) -> None:
        params = {"command": "ls", "args": ["-la", 123, None, True], "timeout_ms": 1000}
        result = parse_exec_params(params)
        assert result.args == ["-la"]

    def test_missing_command_raises(self) -> None:
        params: dict[str, object] = {"args": []}
        with pytest.raises(
            InvalidParamsError,
            match="Missing 'command' or 'argv' parameter",
        ) as exc_info:
            parse_exec_params(params)
        assert "python -m pytest" in str(exc_info.value)

    def test_non_string_command_raises(self) -> None:
        params: dict[str, object] = {"command": 123, "args": []}
        with pytest.raises(InvalidParamsError, match="must be a string or string array"):
            parse_exec_params(params)

    def test_invalid_timeout_uses_default(self) -> None:
        params = {"command": "ls", "args": [], "timeout_ms": -1}
        result = parse_exec_params(params)
        assert result.timeout_ms == DEFAULT_TIMEOUT_MS

    def test_non_int_timeout_uses_default(self) -> None:
        params: dict[str, object] = {"command": "ls", "args": [], "timeout_ms": "fast"}
        result = parse_exec_params(params)
        assert result.timeout_ms == DEFAULT_TIMEOUT_MS

    def test_splits_command_string_into_command_and_args(self) -> None:
        params = {"command": "python -m pytest tests/test_tool_exec.py"}
        result = parse_exec_params(params)
        assert result.command == "python"
        assert result.args == ["-m", "pytest", "tests/test_tool_exec.py"]

    def test_accepts_command_as_argv_list(self) -> None:
        params = {"command": ["python", "-m", "pytest", "tests/test_tool_exec.py"]}
        result = parse_exec_params(params)
        assert result.command == "python"
        assert result.args == ["-m", "pytest", "tests/test_tool_exec.py"]

    def test_accepts_argv_alias_when_command_missing(self) -> None:
        params = {"argv": ["python", "-m", "pytest", "tests/test_tool_exec.py"]}
        result = parse_exec_params(params)
        assert result.command == "python"
        assert result.args == ["-m", "pytest", "tests/test_tool_exec.py"]

    def test_command_argv_list_with_shell_operator_accepted(self) -> None:
        params = {"command": ["ls", "|", "grep", "py"]}
        result = parse_exec_params(params)
        assert result.command == "ls"

    def test_preserves_quoted_spaces_in_command_string(self) -> None:
        params = {"command": "python -c \"print('hello world')\""}
        result = parse_exec_params(params)
        assert result.command == "python"
        assert result.args == ["-c", "print('hello world')"]

    def test_accepts_string_args_and_splits_them(self) -> None:
        params = {"command": "python", "args": "-m pytest tests/test_tool_exec.py"}
        result = parse_exec_params(params)
        assert result.command == "python"
        assert result.args == ["-m", "pytest", "tests/test_tool_exec.py"]

    def test_command_tokens_prepend_explicit_args(self) -> None:
        params = {"command": "python -m pytest", "args": ["-q", "tests/test_tool_exec.py"]}
        result = parse_exec_params(params)
        assert result.command == "python"
        assert result.args == ["-m", "pytest", "-q", "tests/test_tool_exec.py"]

    def test_shell_operator_command_string_accepted(self) -> None:
        params = {"command": "ls | grep py"}
        result = parse_exec_params(params)
        assert result.command == "sh"
        assert result.args == ["-c", "ls | grep py"]

    def test_and_and_shell_operator(self) -> None:
        params = {"command": "git show c9da560 --stat && echo ---"}
        result = parse_exec_params(params)
        assert result.command == "sh"
        assert result.args == ["-c", "git show c9da560 --stat && echo ---"]

    def test_redirection_operator(self) -> None:
        params = {"command": "echo hello > /tmp/test.txt"}
        result = parse_exec_params(params)
        assert result.command == "sh"
        assert result.args == ["-c", "echo hello > /tmp/test.txt"]

    def test_no_operator_preserves_behavior(self) -> None:
        params = {"command": "python -m pytest tests/test_tool_exec.py"}
        result = parse_exec_params(params)
        assert result.command == "python"
        assert result.args == ["-m", "pytest", "tests/test_tool_exec.py"]

    def test_malformed_command_string_raises(self) -> None:
        params = {"command": "python -c \"print('hello')"}
        with pytest.raises(InvalidParamsError):
            parse_exec_params(params)
