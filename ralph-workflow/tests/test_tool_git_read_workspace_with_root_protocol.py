"""Tests for ralph/mcp/tools/git_read.py — MCP git read tool handlers."""

from __future__ import annotations
from tests.mock_workspace_root import MockWorkspaceRoot

from pathlib import Path

from ralph.mcp.tools.git_read import (
    WorkspaceWithRoot,
    run_git_command,
)

CUSTOM_LOG_COUNT = 20

# =============================================================================
# Mock infrastructure
# =============================================================================


class TestWorkspaceWithRootProtocol:
    def test_path_object_satisfies_protocol(self) -> None:
        ws = MockWorkspaceRoot(Path("/tmp"))
        assert isinstance(ws, WorkspaceWithRoot)
        assert ws.root == Path("/tmp")

    def test_str_root_satisfies_protocol(self) -> None:
        result = run_git_command("/tmp", ["--version"])
        assert "git version" in result

