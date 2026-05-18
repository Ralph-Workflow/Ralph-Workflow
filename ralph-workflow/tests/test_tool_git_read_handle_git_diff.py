"""Tests for ralph/mcp/tools/git_read.py — MCP git read tool handlers."""

from __future__ import annotations
from tests.mock_workspace_root import MockWorkspaceRoot
from tests.mock_session import MockSession

from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

from ralph.mcp.tools.coordination import CapabilityDeniedError
from ralph.mcp.tools.git_read import (
    GIT_DIFF_READ_CAPABILITY,
    handle_git_diff,
)

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

        with patch("ralph.mcp.tools.git_read.run_git_command_lenient") as mock_git:
            mock_git.return_value = "diff --staged content"
            result = handle_git_diff(session, workspace, {"args": ["--staged"]})
            assert result.is_error is False


