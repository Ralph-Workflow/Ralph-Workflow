from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

import pytest

from ralph.mcp.tools import git_read
from ralph.mcp.tools.coordination import ToolContent
from ralph.mcp.tools.git_read import (
    ExecutionError,
    handle_git_status,
    run_git_command,
    run_git_command_lenient,
)
from tests.coordination_mock_capable_session import MockCapableSession
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
    assert excinfo.value.timed_out is True


def test_run_git_command_lenient_converts_timeout_to_execution_error(tmp_path: Path) -> None:
    workspace = MockWorkspaceRoot(tmp_path)
    with pytest.raises(ExecutionError) as excinfo:
        run_git_command_lenient(workspace, ["status"], runner=_timeout_runner)
    assert "timed out" in str(excinfo.value).lower()
    assert excinfo.value.timed_out is True


def test_handle_git_status_returns_actionable_is_error_result_on_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _raise_timeout(command: list[str], cwd: object) -> object:
        raise subprocess.TimeoutExpired(cmd=command, timeout=30.0)

    monkeypatch.setattr(git_read, "_run_git_subprocess", _raise_timeout)
    result = handle_git_status(MockCapableSession(), MockWorkspaceRoot(tmp_path), {})

    assert result.is_error is True
    content = result.content[0]
    assert isinstance(content, ToolContent)
    text = content.text.lower()
    assert "timed out" in text
    assert "do not retry" in text
