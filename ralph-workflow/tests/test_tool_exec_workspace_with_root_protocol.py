"""Tests for ralph/mcp/tool_exec.py — MCP exec tool handler."""

from __future__ import annotations

from pathlib import Path

from ralph.mcp.tools.exec import (
    CompletedProcessAdapter,
    ExecRunDeps,
    WorkspaceWithRoot,
    run_command,
)
from tests.mock_workspace_root import MockWorkspaceRoot

CUSTOM_TIMEOUT_MS = 5000
EXPECTED_TIMEOUT_SECONDS = 2.5


class TestWorkspaceWithRootProtocol:
    def test_path_object_satisfies_protocol(self) -> None:
        ws = MockWorkspaceRoot(Path("/tmp"))
        assert isinstance(ws, WorkspaceWithRoot)
        assert ws.root == Path("/tmp")

    def test_str_root_also_works(self, tmp_path: Path) -> None:
        seen: dict[str, Path] = {}

        def runner(
            _command: list[str], cwd: Path, _timeout_seconds: float | None
        ) -> CompletedProcessAdapter:
            seen["cwd"] = cwd
            return CompletedProcessAdapter(stdout=b"test", stderr=b"", returncode=0)

        result = run_command("echo", ["test"], str(tmp_path), 5000, ExecRunDeps(runner=runner))

        assert result.returncode == 0
        assert seen["cwd"] == tmp_path
