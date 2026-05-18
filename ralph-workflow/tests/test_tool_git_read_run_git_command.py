"""Tests for ralph/mcp/tools/git_read.py — MCP git read tool handlers."""

from __future__ import annotations
from tests.mock_workspace_root import MockWorkspaceRoot

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from ralph.mcp.tools.git_read import (
    ExecutionError,
    run_git_command,
)

CUSTOM_LOG_COUNT = 20

# =============================================================================
# Mock infrastructure
# =============================================================================


class TestRunGitCommand:
    def test_successful_git_command(self, tmp_path: Path) -> None:
        # We use 'git' directly since it's available on the test system
        # even if network git operations might be blocked
        result = run_git_command(tmp_path, ["--version"])
        assert "git version" in result

    def test_failing_git_command_raises(self, tmp_path: Path) -> None:
        with pytest.raises(ExecutionError):
            run_git_command(tmp_path, ["nonexistent-subcommand"])

    def test_nonexistent_git_raises_file_not_found(self, tmp_path: Path) -> None:
        with pytest.raises(ExecutionError):
            run_git_command(tmp_path, ["status"])

    def test_uses_injected_runner(self, tmp_path: Path) -> None:
        seen: dict[str, object] = {}
        workspace = MockWorkspaceRoot(tmp_path)

        def fake_runner(command: list[str], cwd: Path) -> object:
            seen["command"] = command
            seen["cwd"] = cwd
            return MagicMock(returncode=0, stdout=b"git ok", stderr=b"")

        result = run_git_command(workspace, ["status"], runner=fake_runner)

        assert result == "git ok"
        assert seen["command"] == ["git", "status"]
        assert seen["cwd"] == tmp_path

    def test_uses_injected_cwd_provider_when_workspace_has_no_root(self) -> None:
        seen: dict[str, object] = {}

        def fake_runner(command: list[str], cwd: Path) -> object:
            seen["cwd"] = cwd
            return MagicMock(returncode=0, stdout=b"git ok", stderr=b"")

        fallback = Path("/virtual/git-fallback")
        run_git_command(object(), ["status"], runner=fake_runner, cwd_provider=lambda: fallback)

        assert seen["cwd"] == fallback

