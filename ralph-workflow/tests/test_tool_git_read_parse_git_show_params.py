from __future__ import annotations

import pytest

from ralph.mcp.tools.coordination import InvalidParamsError
from ralph.mcp.tools.git_read import (
    parse_git_show_params,
)

CUSTOM_LOG_COUNT = 20


class TestParseGitShowParams:
    def test_parses_ref(self) -> None:
        params = {"ref": "HEAD~1"}
        result = parse_git_show_params(params)
        assert result.git_ref == "HEAD~1"

    def test_missing_ref_raises(self) -> None:
        params: dict[str, object] = {}
        with pytest.raises(InvalidParamsError):
            parse_git_show_params(params)

    def test_non_string_ref_raises(self) -> None:
        params = {"ref": 123}
        with pytest.raises(InvalidParamsError):
            parse_git_show_params(params)

    def test_rejects_a_flag_shaped_ref_that_would_write_a_file(self) -> None:
        for ref in ("--output=/tmp/pwn", "--outp\x00ut=/tmp/pwn", "-o/tmp/pwn"):
            with pytest.raises(InvalidParamsError):
                parse_git_show_params({"ref": ref})

    def test_rejects_an_embedded_nul_in_a_revision(self) -> None:
        with pytest.raises(InvalidParamsError, match="embedded NUL"):
            parse_git_show_params({"ref": "HEAD~\x001"})

    def test_accepts_an_ordinary_revision(self) -> None:
        assert parse_git_show_params({"ref": "HEAD~2"}).git_ref == "HEAD~2"
