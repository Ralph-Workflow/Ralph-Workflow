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

    class MockSession:
        session_id = "test-session"

        def __init__(self, *args: object) -> None:
            if not args:
                self._caps: set[str] = set()
            elif len(args) == 1 and isinstance(args[0], set):
                self._caps = {s for s in args[0] if isinstance(s, str)}
            else:
                self._caps = {s for s in args if isinstance(s, str)}

        def check_capability(self, capability: str) -> object:
            return capability in self._caps

    class MockWorkspaceRoot:
        def __init__(self, root: object) -> None:
            self.root = root


MockSession = TestHandleGitShow.MockSession
MockWorkspaceRoot = TestHandleGitShow.MockWorkspaceRoot
