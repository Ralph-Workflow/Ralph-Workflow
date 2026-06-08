"""Regression tests: MCP git read tools are bounded and fail closed on timeout.

`git status`/`git diff`/etc. over a repo with large `vendor/` submodules (or a
held `.git` lock) could block the MCP server thread forever, starving the agent
of output and tripping the idle watchdog. The git runner must bound the
subprocess and convert a timeout into a clean `ExecutionError` (which the MCP
server surfaces as a -32603 tool error), so the agent fails fast and continues.
"""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

import pytest

from ralph.mcp.tools.git_read import (
    ExecutionError,
    run_git_command,
    run_git_command_lenient,
)
from tests.mock_workspace_root import MockWorkspaceRoot

if TYPE_CHECKING:
    from pathlib import Path


def _timeout_runner(command: list[str], cwd: object) -> object:
    raise subprocess.TimeoutExpired(cmd=command, timeout=30.0)


def test_run_git_command_converts_timeout_to_execution_error(tmp_path: Path) -> None:
    workspace = MockWorkspaceRoot(tmp_path)
    with pytest.raises(ExecutionError) as excinfo:
        run_git_command(workspace, ["status"], runner=_timeout_runner)
    assert "timed out" in str(excinfo.value).lower()


def test_run_git_command_lenient_converts_timeout_to_execution_error(tmp_path: Path) -> None:
    workspace = MockWorkspaceRoot(tmp_path)
    with pytest.raises(ExecutionError) as excinfo:
        run_git_command_lenient(workspace, ["status"], runner=_timeout_runner)
    assert "timed out" in str(excinfo.value).lower()
