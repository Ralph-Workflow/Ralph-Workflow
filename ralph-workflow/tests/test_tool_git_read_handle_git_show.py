"""Tests for ralph/mcp/tools/git_read.py — MCP git show handler (Phase 4)."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

from ralph.mcp.tools.coordination import CapabilityDeniedError, InvalidParamsError
from ralph.mcp.tools.git_read import (
    GIT_STATUS_READ_CAPABILITY,
    ExecutionError,
    handle_git_show,
)
from tests.mock_session import MockSession
from tests.mock_workspace_root import MockWorkspaceRoot

if TYPE_CHECKING:
    from pathlib import Path

CUSTOM_LOG_COUNT = 20


# AC-06 / analysis-feedback regression: see
# ``test_tool_git_read_handle_git_log.py`` for the precedent.
# ``_build_git_show_summary_payload`` previously computed
# ``bytes_out`` BEFORE adding the field, so the declared value
# was smaller than the actual returned text. This regression pins
# the new convention that declared ``bytes_out`` equals the actual
# UTF-8 length of the final serialized envelope the caller sees.
def test_format_summary_bytes_out_matches_actual_payload(
    tmp_path: Path,
) -> None:
    session = MockSession({GIT_STATUS_READ_CAPABILITY})
    workspace = MockWorkspaceRoot(tmp_path)
    raw = (
        "commit abc1234def5678abc1234def5678abc1234def5\n"
        "Author: Test <test@example.com>\n"
        "Date:   Tue Jan 1 00:00:00 2026 +0000\n"
        "\n"
        "    hello subject\n"
    )
    with patch("ralph.mcp.tools.git_read.run_git_command") as mock_git:
        mock_git.return_value = raw
        result = handle_git_show(
            session, workspace, {"ref": "HEAD", "format": "summary"}
        )
    envelope = json.loads(result.content[0].text)
    assert envelope["bytes_out"] == len(result.content[0].text.encode("utf-8"))


# =============================================================================
# Tests
# =============================================================================


class TestHandleGitShow:
    def test_show_requires_capability(self, tmp_path: Path) -> None:
        session = MockSession(set())
        workspace = MockWorkspaceRoot(tmp_path)

        with pytest.raises(CapabilityDeniedError):
            handle_git_show(session, workspace, {"ref": "HEAD"})

    def test_show_requires_ref_param(self, tmp_path: Path) -> None:
        session = MockSession({GIT_STATUS_READ_CAPABILITY})
        workspace = MockWorkspaceRoot(tmp_path)

        with pytest.raises(InvalidParamsError):
            handle_git_show(session, workspace, {})

    def test_show_with_nonexistent_ref_returns_error(self, tmp_path: Path) -> None:
        session = MockSession({GIT_STATUS_READ_CAPABILITY})
        workspace = MockWorkspaceRoot(tmp_path)

        # This should raise ExecutionError since the ref doesn't exist
        with pytest.raises(ExecutionError):
            handle_git_show(session, workspace, {"ref": "DOES_NOT_EXIST_12345"})

    # -- Phase 4: format=summary ----------------------------------------------

    def test_format_summary_returns_compact_envelope(self, tmp_path: Path) -> None:
        session = MockSession({GIT_STATUS_READ_CAPABILITY})
        workspace = MockWorkspaceRoot(tmp_path)

        with patch("ralph.mcp.tools.git_read.run_git_command") as mock_git:
            mock_git.return_value = (
                "abcdef0123456789\x1fabc1234\x1fAuthor Name\x1f"
                "author@example.com\x1f"
                "Wed Jul 10 12:00:00 2024 +0000\x1f"
                "Initial commit\x1f\x1f\x1f"
                "abcdef0123456789"
            )
            result = handle_git_show(
                session, workspace, {"ref": "HEAD", "format": "summary"}
            )
            assert result.is_error is False
            envelope = json.loads(result.content[0].text)
            assert envelope["format"] == "summary"
            assert envelope["ref"] == "HEAD"
            assert envelope["kind"] == "commit"
            assert envelope["sha"] == "abcdef0123456789"
            assert envelope["short_sha"] == "abc1234"
            assert envelope["author_name"] == "Author Name"
            assert envelope["author_email"] == "author@example.com"
            assert envelope["subject"] == "Initial commit"
            assert envelope["parents"] == []
            assert envelope["truncated"] is False
            assert envelope["bytes_in"] > 0
            assert envelope["bytes_out"] > 0

    def test_format_raw_default_unchanged(self, tmp_path: Path) -> None:
        """``format='raw'`` (the default) preserves the legacy git show text."""
        session = MockSession({GIT_STATUS_READ_CAPABILITY})
        workspace = MockWorkspaceRoot(tmp_path)

        with patch("ralph.mcp.tools.git_read.run_git_command") as mock_git:
            mock_git.return_value = "commit abc1234\nAuthor: ...\n\n    diff body"
            result = handle_git_show(session, workspace, {"ref": "HEAD"})
            assert result.is_error is False
            # ``mock_git`` is called as ``run_git_command(workspace, git_args)``.
            args = mock_git.call_args.args
            assert args[0] is workspace
            assert args[1] == ["show", "HEAD"]
            assert "commit abc1234" in result.content[0].text

    def test_format_invalid_value_raises_invalid_params(
        self, tmp_path: Path
    ) -> None:
        session = MockSession({GIT_STATUS_READ_CAPABILITY})
        workspace = MockWorkspaceRoot(tmp_path)

        with pytest.raises(InvalidParamsError, match="Invalid git_show format"):
            handle_git_show(session, workspace, {"ref": "HEAD", "format": "bogus"})
