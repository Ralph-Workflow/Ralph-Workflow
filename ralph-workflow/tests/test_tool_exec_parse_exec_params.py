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
from ralph.timeout_defaults import EXEC_MAX_TIMEOUT_MS

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

    def test_zero_timeout_uses_default_not_unbounded(self) -> None:
        # timeout_ms=0 must NOT mean "no timeout": it would become an unbounded
        # blocking call on the MCP server thread (an agent-controllable hang).
        params = {"command": "ls", "args": [], "timeout_ms": 0}
        result = parse_exec_params(params)
        assert result.timeout_ms == DEFAULT_TIMEOUT_MS

    def test_timeout_is_capped_at_max(self) -> None:
        # An over-large timeout_ms must be capped so a tool call can never outrun
        # the MCP client request timeout (which would re-trigger -32001).
        params = {"command": "ls", "args": [], "timeout_ms": EXEC_MAX_TIMEOUT_MS * 10}
        result = parse_exec_params(params)
        assert result.timeout_ms == EXEC_MAX_TIMEOUT_MS

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

    def test_shell_operator_command_string_becomes_shell_command(self) -> None:
        """A pipe in a command STRING routes through ``sh -c``: the raw
        string is preserved in ``shell_command`` and ``command`` / ``args``
        carry the first pipeline segment for a readable result header.
        """
        result = parse_exec_params({"command": "ls | grep py"})
        assert result.shell_command == "ls | grep py"
        assert result.command == "ls"
        assert result.args == []

    def test_and_and_shell_operator_becomes_shell_command(self) -> None:
        result = parse_exec_params({"command": "git show c9da560 --stat && echo ---"})
        assert result.shell_command == "git show c9da560 --stat && echo ---"
        assert result.command == "git"
        assert result.args == ["show", "c9da560", "--stat"]

    def test_redirection_operator_becomes_shell_command(self) -> None:
        result = parse_exec_params({"command": "echo hello > /tmp/test.txt"})
        assert result.shell_command == "echo hello > /tmp/test.txt"
        assert result.command == "echo"
        assert result.args == ["hello"]

    def test_semicolon_shell_operator_becomes_shell_command(self) -> None:
        result = parse_exec_params({"command": "echo safe; ls -la"})
        assert result.shell_command == "echo safe; ls -la"
        assert result.command == "echo"
        assert result.args == ["safe"]

    def test_argv_list_operator_stays_literal_not_shell(self) -> None:
        """An argv LIST is explicit argv: operator tokens stay literal and
        the invocation is NOT shell-interpreted (shell_command is None)."""
        result = parse_exec_params({"command": ["ls", "|", "grep", "py"]})
        assert result.shell_command is None
        assert result.command == "ls"
        assert result.args == ["|", "grep", "py"]

    def test_no_operator_preserves_behavior(self) -> None:
        params = {"command": "python -m pytest tests/test_tool_exec.py"}
        result = parse_exec_params(params)
        assert result.command == "python"
        assert result.args == ["-m", "pytest", "tests/test_tool_exec.py"]

    def test_malformed_command_string_raises(self) -> None:
        params = {"command": "python -c \"print('hello')"}
        with pytest.raises(InvalidParamsError):
            parse_exec_params(params)

    def test_quoted_metacharacter_remains_valid_argv(self) -> None:
        """AC-11 backward compatibility: ``printf '>'`` and other
        quoted literals are valid argv and must NOT be rejected.
        The previous raw-character precheck broke this case by
        treating any ``| & ; < >`` in the input as compound
        shell. The quote-aware walker allows the literal ``>``
        because it sits inside single quotes.
        """
        params = {"command": "printf '>'"}
        result = parse_exec_params(params)
        assert result.command == "printf"
        assert result.args == [">"]

    def test_double_quoted_metacharacter_remains_valid_argv(self) -> None:
        """AC-11: ``grep "a|b"`` (double-quoted shell
        metacharacter inside the argument) must parse without
        rejection.
        """
        params = {"command": 'grep "a|b"'}
        result = parse_exec_params(params)
        assert result.command == "grep"
        assert result.args == ["a|b"]

    def test_unquoted_compound_command_becomes_shell(self) -> None:
        """An unquoted ``;`` produces a shell command; the blacklist that
        guards the embedded ``curl`` is enforced later, per pipeline
        segment, in ``handle_exec_command`` (not at parse time)."""
        result = parse_exec_params({"command": "echo safe; ls -la"})
        assert result.shell_command == "echo safe; ls -la"
        assert result.command == "echo"

    def test_unquoted_pipe_preserves_first_segment_args(self) -> None:
        result = parse_exec_params({"command": "grep -r foo . | wc -l"})
        assert result.shell_command == "grep -r foo . | wc -l"
        assert result.command == "grep"
        assert result.args == ["-r", "foo", "."]

    def test_quoted_operator_is_not_shell(self) -> None:
        """A fully-quoted operator is literal argv, not shell: ``shell_command``
        stays None so the command runs argv-direct."""
        result = parse_exec_params({"command": "printf '>'"})
        assert result.shell_command is None
        assert result.command == "printf"
        assert result.args == [">"]
