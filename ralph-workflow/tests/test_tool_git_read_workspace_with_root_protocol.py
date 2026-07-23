"""Tests for ralph/mcp/tools/git_read.py — MCP git read tool handlers."""

from __future__ import annotations

import subprocess
from pathlib import Path

from ralph.mcp.tools.git_read import (
    WorkspaceWithRoot,
    run_git_command,
)
from tests.mock_workspace_root import MockWorkspaceRoot

CUSTOM_LOG_COUNT = 20

# =============================================================================
# Mock infrastructure
# =============================================================================


class TestWorkspaceWithRootProtocol:
    def test_path_object_satisfies_protocol(self) -> None:
        ws = MockWorkspaceRoot(Path("/tmp"))
        assert isinstance(ws, WorkspaceWithRoot)
        assert ws.root == Path("/tmp")

    def test_str_workspace_uses_injected_fallback_cwd(self) -> None:
        seen: list[Path] = []
        fallback = Path("/virtual/fallback")

        def fake_runner(
            command: list[str], cwd: Path
        ) -> subprocess.CompletedProcess[bytes]:
            seen.append(cwd)
            return subprocess.CompletedProcess(
                command,
                returncode=0,
                stdout=b"clean",
                stderr=b"",
            )

        result = run_git_command(
            "/tmp",
            ["status"],
            runner=fake_runner,
            cwd_provider=lambda: fallback,
        )

        assert result == "clean"
        assert seen == [fallback]
