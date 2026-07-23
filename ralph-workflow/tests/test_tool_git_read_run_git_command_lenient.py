"""Tests for ralph/mcp/tools/git_read.py — MCP git read tool handlers."""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

from ralph.mcp.tools.git_read import (
    lenient_stdout,
    run_git_command_lenient,
)

if TYPE_CHECKING:
    from pathlib import Path

CUSTOM_LOG_COUNT = 20

# =============================================================================
# Mock infrastructure
# =============================================================================


class TestRunGitCommandLenient:
    def test_returns_output_regardless_of_exit_code(self, tmp_path: Path) -> None:
        def failing_runner(
            command: list[str], cwd: Path
        ) -> subprocess.CompletedProcess[bytes]:
            return subprocess.CompletedProcess(
                command,
                returncode=1,
                stdout=b"partial status",
                stderr=b"simulated failure",
            )

        result = run_git_command_lenient(
            tmp_path,
            ["status"],
            runner=failing_runner,
        )

        assert result.returncode == 1
        assert lenient_stdout(result) == "partial statussimulated failure"
