"""Tests for ralph/mcp/tools/git_read.py — MCP git read tool handlers."""

from __future__ import annotations

from typing import TYPE_CHECKING

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

# =============================================================================
# Mock infrastructure
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
