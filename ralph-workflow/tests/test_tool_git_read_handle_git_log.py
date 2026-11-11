from __future__ import annotations

import json
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

from ralph.mcp.tools.coordination import CapabilityDeniedError, InvalidParamsError
from ralph.mcp.tools.git_read import (
    GIT_STATUS_READ_CAPABILITY,
    handle_git_log,
)
from tests.mock_session import MockSession
from tests.mock_workspace_root import MockWorkspaceRoot

if TYPE_CHECKING:
    from pathlib import Path

CUSTOM_LOG_COUNT = 20


@pytest.fixture(autouse=True)
def _resolve_cwd_without_git_probe(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        "ralph.mcp.tools.git_read.resolve_git_cwd",
        lambda **_kwargs: (tmp_path, False, None),
    )


def test_format_summary_bytes_out_matches_actual_payload(
    tmp_path: Path,
) -> None:
    session = MockSession({GIT_STATUS_READ_CAPABILITY})
    workspace = MockWorkspaceRoot(tmp_path)
    payload_text = "abc1234 first commit\ndef5678 second commit\n"
    with patch("ralph.mcp.tools.git_read.run_git_command") as mock_git:
        mock_git.return_value = payload_text
        result = handle_git_log(session, workspace, {"format": "summary", "count": 5})
    envelope = json.loads(result.content[0].text)
    assert envelope["bytes_out"] == len(result.content[0].text.encode("utf-8"))


class TestHandleGitLog:
    def test_log_requires_capability(self, tmp_path: Path) -> None:
        session = MockSession(set())
        workspace = MockWorkspaceRoot(tmp_path)

        with pytest.raises(CapabilityDeniedError):
            handle_git_log(session, workspace, {})

    def test_log_accepts_count(self, tmp_path: Path) -> None:
        session = MockSession({GIT_STATUS_READ_CAPABILITY})
        workspace = MockWorkspaceRoot(tmp_path)

        with patch("ralph.mcp.tools.git_read.run_git_command") as mock_git:
            mock_git.return_value = "abc123 commit message"
            result = handle_git_log(session, workspace, {"count": 5})
            assert result.is_error is False
            assert result.content
            assert "abc123 commit message" in result.content[0].text

    def test_format_summary_returns_compact_envelope(self, tmp_path: Path) -> None:
        session = MockSession({GIT_STATUS_READ_CAPABILITY})
        workspace = MockWorkspaceRoot(tmp_path)

        with patch("ralph.mcp.tools.git_read.run_git_command") as mock_git:
            mock_git.return_value = "abc1234 first commit\ndef5678 second commit\n"
            result = handle_git_log(session, workspace, {"count": 5, "format": "summary"})
            assert result.is_error is False
            envelope = json.loads(result.content[0].text)
            assert envelope["format"] == "summary"
            assert envelope["count"] == 2
            assert envelope["commits"] == [
                {
                    "short_sha": "abc1234",
                    "sha": "abc1234",
                    "subject": "first commit",
                },
                {
                    "short_sha": "def5678",
                    "sha": "def5678",
                    "subject": "second commit",
                },
            ]
            assert envelope["bytes_in"] == len(b"abc1234 first commit\ndef5678 second commit\n")
            assert envelope["bytes_out"] > 0

    def test_format_raw_default_unchanged(self, tmp_path: Path) -> None:
        session = MockSession({GIT_STATUS_READ_CAPABILITY})
        workspace = MockWorkspaceRoot(tmp_path)

        with patch("ralph.mcp.tools.git_read.run_git_command") as mock_git:
            mock_git.return_value = "abc1234 first commit"
            result = handle_git_log(session, workspace, {})
            assert result.is_error is False
            args = mock_git.call_args.args
            assert args[0] is workspace
            assert args[1] == ["log", "-10", "--oneline"]
            assert "abc1234 first commit" in result.content[0].text

    def test_format_invalid_value_raises_invalid_params(self, tmp_path: Path) -> None:
        session = MockSession({GIT_STATUS_READ_CAPABILITY})
        workspace = MockWorkspaceRoot(tmp_path)

        with pytest.raises(InvalidParamsError, match="Invalid git_log format"):
            handle_git_log(session, workspace, {"format": "bogus"})
