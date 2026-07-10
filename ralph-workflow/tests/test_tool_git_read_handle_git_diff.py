"""Tests for ralph/mcp/tools/git_read.py — MCP git read tool handlers."""

from __future__ import annotations

import json
import subprocess
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

from ralph.mcp.tools.coordination import CapabilityDeniedError, InvalidParamsError
from ralph.mcp.tools.git_read import (
    GIT_DIFF_READ_CAPABILITY,
    handle_git_diff,
)
from tests.mock_session import MockSession
from tests.mock_workspace_root import MockWorkspaceRoot

if TYPE_CHECKING:
    from pathlib import Path

CUSTOM_LOG_COUNT = 20

# =============================================================================
# Mock infrastructure
# =============================================================================


class TestHandleGitDiff:
    def test_diff_requires_capability(self, tmp_path: Path) -> None:
        session = MockSession(set())
        workspace = MockWorkspaceRoot(tmp_path)

        with pytest.raises(CapabilityDeniedError):
            handle_git_diff(session, workspace, {"args": []})

    def test_diff_accepts_args(self, tmp_path: Path) -> None:
        session = MockSession({GIT_DIFF_READ_CAPABILITY})
        workspace = MockWorkspaceRoot(tmp_path)

        completed = subprocess.CompletedProcess(
            args=["git", "diff", "--staged"],
            returncode=0,
            stdout=b"diff --staged content",
            stderr=b"",
        )
        with patch(
            "ralph.mcp.tools.git_read.run_git_command_lenient",
            return_value=completed,
        ) as mock_git:
            result = handle_git_diff(session, workspace, {"args": ["--staged"]})
            assert result.is_error is False
            assert mock_git.called

    # --- AC-06: max_bytes is strictly bounded -------------------------------

    def test_diff_summary_rejects_negative_max_bytes(
        self, tmp_path: Path
    ) -> None:
        """AC-06: ``max_bytes`` must be a positive integer in
        ``[1, 50000]``. The previous lenient coercion silently
        truncated a 10-byte diff to ``[:-1]`` for ``max_bytes=-1``
        and surfaced a misleading ``truncated=true`` payload.
        The strict contract fails closed.
        """
        session = MockSession({GIT_DIFF_READ_CAPABILITY})
        workspace = MockWorkspaceRoot(tmp_path)
        with pytest.raises(InvalidParamsError):
            handle_git_diff(
                session,
                workspace,
                {"format": "summary", "max_bytes": -1},
            )

    def test_diff_summary_rejects_zero_max_bytes(
        self, tmp_path: Path
    ) -> None:
        session = MockSession({GIT_DIFF_READ_CAPABILITY})
        workspace = MockWorkspaceRoot(tmp_path)
        with pytest.raises(InvalidParamsError):
            handle_git_diff(
                session,
                workspace,
                {"format": "summary", "max_bytes": 0},
            )

    def test_diff_summary_rejects_bool_max_bytes(
        self, tmp_path: Path
    ) -> None:
        session = MockSession({GIT_DIFF_READ_CAPABILITY})
        workspace = MockWorkspaceRoot(tmp_path)
        with pytest.raises(InvalidParamsError):
            handle_git_diff(
                session,
                workspace,
                {"format": "summary", "max_bytes": True},
            )

    def test_diff_summary_rejects_malformed_string_max_bytes(
        self, tmp_path: Path
    ) -> None:
        session = MockSession({GIT_DIFF_READ_CAPABILITY})
        workspace = MockWorkspaceRoot(tmp_path)
        with pytest.raises(InvalidParamsError):
            handle_git_diff(
                session,
                workspace,
                {"format": "summary", "max_bytes": "not-an-int"},
            )

    def test_diff_summary_rejects_non_integer_float_max_bytes(
        self, tmp_path: Path
    ) -> None:
        session = MockSession({GIT_DIFF_READ_CAPABILITY})
        workspace = MockWorkspaceRoot(tmp_path)
        with pytest.raises(InvalidParamsError):
            handle_git_diff(
                session,
                workspace,
                {"format": "summary", "max_bytes": 1.5},
            )

    def test_diff_summary_rejects_oversized_max_bytes(
        self, tmp_path: Path
    ) -> None:
        session = MockSession({GIT_DIFF_READ_CAPABILITY})
        workspace = MockWorkspaceRoot(tmp_path)
        with pytest.raises(InvalidParamsError):
            handle_git_diff(
                session,
                workspace,
                {"format": "summary", "max_bytes": 1_000_000},
            )

    def test_diff_summary_accepts_positive_max_bytes(
        self, tmp_path: Path
    ) -> None:
        """AC-06: a positive ``max_bytes`` returns a real
        summary payload with a capped excerpt.
        """
        session = MockSession({GIT_DIFF_READ_CAPABILITY})
        workspace = MockWorkspaceRoot(tmp_path)
        diff_text = b"1234567890"
        numstat = subprocess.CompletedProcess(
            args=["git", "diff", "--numstat"],
            returncode=0,
            stdout=b"1\t0\ta.py\n",
            stderr=b"",
        )
        full = subprocess.CompletedProcess(
            args=["git", "diff"],
            returncode=0,
            stdout=diff_text,
            stderr=b"",
        )
        with patch(
            "ralph.mcp.tools.git_read.run_git_command_lenient",
            side_effect=[numstat, full],
        ):
            result = handle_git_diff(
                session,
                workspace,
                {"format": "summary", "max_bytes": 5},
            )
        assert result.is_error is False
        payload = json.loads(result.content[0].text)
        assert payload["format"] == "summary"
        assert payload["max_bytes"] == 5
        assert payload["truncated"] is True
        # The excerpt must equal ``diff_text[:5]`` (5 bytes),
        # proving the bounded contract is honored.
        assert payload["diff_excerpt"] == diff_text[:5].decode("utf-8")
