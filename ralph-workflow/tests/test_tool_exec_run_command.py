"""Tests for ralph/mcp/tool_exec.py — MCP exec tool handler."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from ralph.mcp.tools.exec import (
    ExecRunDeps,
    ExecutionError,
    run_command,
)

CUSTOM_TIMEOUT_MS = 5000
EXPECTED_TIMEOUT_SECONDS = 2.5


class TestRunCommand:
    def test_successful_command(self, tmp_path: Path) -> None:
        result = run_command("echo", ["hello"], tmp_path, 5000)
        assert result.returncode == 0
        assert "hello" in result.stdout.decode()

    def test_failing_command(self, tmp_path: Path) -> None:
        result = run_command("false", [], tmp_path, 5000)
        assert result.returncode != 0

    def test_file_not_found_raises_execution_error(self, tmp_path: Path) -> None:
        with pytest.raises(ExecutionError):
            run_command("nonexistent_command_xyz", [], tmp_path, 5000)

    def test_zero_timeout_means_no_timeout(self, tmp_path: Path) -> None:
        result = run_command("echo", ["test"], tmp_path, 0)
        assert result.returncode == 0

    def test_workspace_with_str_root(self, tmp_path: Path) -> None:
        result = run_command("echo", ["test"], str(tmp_path), 5000)
        assert result.returncode == 0

    def test_uses_injected_cwd_provider_when_workspace_has_no_root(self) -> None:
        seen: dict[str, object] = {}

        def fake_runner(command: list[str], cwd: Path, timeout_seconds: float | None) -> object:
            seen["cwd"] = cwd
            return MagicMock(returncode=0, stdout=b"ok", stderr=b"")

        fallback = Path("/virtual/fallback")
        run_command(
            "python",
            ["--version"],
            object(),
            1000,
            deps=ExecRunDeps(runner=fake_runner, cwd_provider=lambda: fallback),
        )

        assert seen["cwd"] == fallback

    def test_uses_injected_runner(self, tmp_path: Path) -> None:
        seen: dict[str, object] = {}
        workspace = MockWorkspaceRoot(tmp_path)

        def fake_runner(command: list[str], cwd: Path, timeout_seconds: float | None) -> object:
            seen["command"] = command
            seen["cwd"] = cwd
            seen["timeout"] = timeout_seconds
            return MagicMock(returncode=0, stdout=b"ok", stderr=b"")

        result = run_command(
            "python",
            ["--version"],
            workspace,
            2500,
            deps=ExecRunDeps(runner=fake_runner),
        )

        assert result.returncode == 0
        assert seen["command"] == ["python", "--version"]
        assert seen["cwd"] == tmp_path
        assert seen["timeout"] == EXPECTED_TIMEOUT_SECONDS

    class MockWorkspaceRoot:
        def __init__(self, root: object) -> None:
            self.root = root


MockWorkspaceRoot = TestRunCommand.MockWorkspaceRoot
