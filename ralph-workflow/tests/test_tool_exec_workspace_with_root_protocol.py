"""Tests for ralph/mcp/tool_exec.py — MCP exec tool handler."""

from __future__ import annotations

from pathlib import Path

from ralph.mcp.tools.exec import (
    WorkspaceWithRoot,
    run_command,
)

CUSTOM_TIMEOUT_MS = 5000
EXPECTED_TIMEOUT_SECONDS = 2.5


class TestWorkspaceWithRootProtocol:
    def test_path_object_satisfies_protocol(self) -> None:
        ws = MockWorkspaceRoot(Path("/tmp"))
        assert isinstance(ws, WorkspaceWithRoot)
        assert ws.root == Path("/tmp")

    def test_str_root_also_works(self, tmp_path: Path) -> None:
        # The _workspace_root helper should handle string roots
        result = run_command("echo", ["test"], str(tmp_path), 5000)
        assert result.returncode == 0

    class MockWorkspaceRoot:
        def __init__(self, root: object) -> None:
            self.root = root


MockWorkspaceRoot = TestWorkspaceWithRootProtocol.MockWorkspaceRoot
