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
        params = {"command": "ls", "args": [], "timeout_ms": 0}
        result = parse_exec_params(params)
        assert result.timeout_ms == DEFAULT_TIMEOUT_MS

    def test_timeout_is_capped_at_max(self) -> None:
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
        result = parse_exec_params({"command": ["ls", "|", "grep", "py"]})
        assert result.shell_command is None
        assert result.command == "ls"
        assert result.args == ["|", "grep", "py"]

    def test_no_operator_preserves_behavior(self) -> None:
        params = {"command": "python -m pytest tests/test_tool_exec.py"}
        result = parse_exec_params(params)
        assert result.command == "python"
        assert result.args == ["-m", "pytest", "tests/test_tool_exec.py"]

    def test_exec_regression_leading_environment_assignment_uses_shell(self) -> None:
        source = "EXPEDITIONS_TEST_DB_NAME=test bin/rails test test/models/user_test.rb"

        result = parse_exec_params({"command": source})

        assert result.shell_command == source
        assert result.command == "bin/rails"
        assert result.args == ["test", "test/models/user_test.rb"]

    def test_exec_regression_environment_assignment_preserves_explicit_args(self) -> None:
        result = parse_exec_params({"command": "X=1 printf", "args": ["%s", "hello world"]})

        assert result.shell_command == "X=1 printf %s 'hello world'"
        assert result.command == "printf"
        assert result.args == ["%s", "hello world"]

    def test_malformed_command_string_raises(self) -> None:
        params = {"command": "python -c \"print('hello')"}
        with pytest.raises(InvalidParamsError):
            parse_exec_params(params)

    def test_quoted_metacharacter_remains_valid_argv(self) -> None:
        params = {"command": "printf '>'"}
        result = parse_exec_params(params)
        assert result.command == "printf"
        assert result.args == [">"]

    def test_double_quoted_metacharacter_remains_valid_argv(self) -> None:
        params = {"command": 'grep "a|b"'}
        result = parse_exec_params(params)
        assert result.command == "grep"
        assert result.args == ["a|b"]

    def test_unquoted_compound_command_becomes_shell(self) -> None:
        result = parse_exec_params({"command": "echo safe; ls -la"})
        assert result.shell_command == "echo safe; ls -la"
        assert result.command == "echo"

    def test_unquoted_pipe_preserves_first_segment_args(self) -> None:
        result = parse_exec_params({"command": "grep -r foo . | wc -l"})
        assert result.shell_command == "grep -r foo . | wc -l"
        assert result.command == "grep"
        assert result.args == ["-r", "foo", "."]

    def test_quoted_operator_is_not_shell(self) -> None:
        result = parse_exec_params({"command": "printf '>'"})
        assert result.shell_command is None
        assert result.command == "printf"
        assert result.args == [">"]

    def test_rejects_an_embedded_nul_that_would_hide_a_blacklisted_command(self) -> None:
        for params in (
            {"command": "su\x00do rm -rf /"},
            {"command": ["su\x00do", "-i"]},
            {"command": "echo hi", "args": ["--flag\x00"]},
            {"argv": "su\x00do -i"},
        ):
            with pytest.raises(InvalidParamsError, match="embedded NUL"):
                parse_exec_params(params)

    def test_accepts_a_clean_command_unchanged(self) -> None:
        result = parse_exec_params({"command": "echo hi"})
        assert result.command == "echo"
        assert result.args == ["hi"]

    @pytest.mark.parametrize(
        "command",
        [
            '["cat", "tsconfig.json"]',
            "['cat', 'tsconfig.json']",
            '  ["cat","tsconfig.json"]  ',
        ],
    )
    def test_stringified_argv_list_is_read_as_argv(self, command: str) -> None:
        for key in ("command", "argv"):
            result = parse_exec_params({key: command})
            assert result.command == "cat"
            assert result.args == ["tsconfig.json"]
            assert result.shell_command is None

    def test_stringified_argv_list_keeps_quoted_operators_literal(self) -> None:
        result = parse_exec_params({"command": '["grep", "-E", "a|b", "file.txt"]'})
        assert result.command == "grep"
        assert result.args == ["-E", "a|b", "file.txt"]
        assert result.shell_command is None

    def test_stringified_argv_list_merges_explicit_args(self) -> None:
        result = parse_exec_params({"command": '["npm", "run"]', "args": ["lint"]})
        assert result.command == "npm"
        assert result.args == ["run", "lint"]

    @pytest.mark.parametrize(
        ("command", "expected_command", "expected_args"),
        [
            ('[ "$x" ]', "[", ["$x", "]"]),
            ("[ -f package.json ]", "[", ["-f", "package.json", "]"]),
            ("[[ -n $x ]]", "[[", ["-n", "$x", "]]"]),
            ("[1, 2]", "[1,", ["2]"]),
            ('["cat", 3]', "[cat,", ["3]"]),
            ("[]", "[]", []),
            ('["unterminated"', "[unterminated", []),
            ('["a", {[1]: 2}]', "[a,", ["{[1]:", "2}]"]),
            ('["a", {1, [2]}]', "[a,", ["{1,", "[2]}]"]),
        ],
    )
    def test_bracketed_text_that_is_not_a_string_list_is_left_alone(
        self, command: str, expected_command: str, expected_args: list[str]
    ) -> None:
        result = parse_exec_params({"command": command})
        assert result.command == expected_command
        assert result.args == expected_args

    def test_stringified_argv_cannot_smuggle_a_nul_past_the_nul_guard(self) -> None:
        for command in ('["su\\u0000do", "-i"]', "['su\\x00do', '-i']"):
            with pytest.raises(InvalidParamsError, match="embedded NUL"):
                parse_exec_params({"command": command})
