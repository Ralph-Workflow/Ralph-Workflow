from __future__ import annotations

import pytest

from ralph.mcp.tools.coordination import InvalidParamsError
from ralph.mcp.tools.git_read import (
    parse_git_diff_params,
)

CUSTOM_LOG_COUNT = 20


class TestParseGitDiffParams:
    def test_parses_string_args(self) -> None:
        params = {"args": ["--staged", "--name-only"]}
        result = parse_git_diff_params(params)
        assert result.args == ["--staged", "--name-only"]

    def test_filters_non_string_args(self) -> None:
        params = {"args": ["--staged", 123, None, True, "--name-only"]}
        result = parse_git_diff_params(params)
        assert result.args == ["--staged", "--name-only"]

    def test_empty_args_for_non_list(self) -> None:
        params = {"args": "not a list"}
        result = parse_git_diff_params(params)
        assert result.args == []

    def test_missing_args_returns_empty_list(self) -> None:
        params: dict[str, object] = {}
        result = parse_git_diff_params(params)
        assert result.args == []

    def test_rejects_an_embedded_nul_that_would_hide_a_denied_flag(self) -> None:
        for hidden in ("--ext-di\x00ff", "--out\x00put=/tmp/pwn"):
            with pytest.raises(InvalidParamsError, match="embedded NUL"):
                parse_git_diff_params({"args": [hidden]})
