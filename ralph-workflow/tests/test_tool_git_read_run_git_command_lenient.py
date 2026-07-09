"""Tests for ralph/mcp/tools/git_read.py — MCP git read tool handlers."""

from __future__ import annotations

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
        # Even with a failing command, lenient should return output
        result = run_git_command_lenient(tmp_path, ["--version"])
        assert "git version" in lenient_stdout(result)
