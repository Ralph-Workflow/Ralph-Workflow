"""Tests for ralph/mcp/tools/unsafe_exec.py -- MCP unsafe_exec tool handler."""
# ruff: noqa: I001 -- ruff I001 is idempotency-unstable for this file's import block

from __future__ import annotations

import subprocess
import types
from typing import TYPE_CHECKING

import pytest

from ralph.mcp.tools.coordination import (
    CapabilityDeniedError,
    InvalidParamsError,
    ToolContent,
)
from ralph.mcp.tools.unsafe_exec import (
    PROCESS_EXEC_UNBOUNDED_CAPABILITY,
    _VCS_COMMANDS,
    handle_unsafe_exec,
)
from tests.mock_session import MockSession
from tests.mock_workspace_root import MockWorkspaceRoot

if TYPE_CHECKING:
    from pathlib import Path


def _make_completed_process(
    stdout: bytes = b"",
    stderr: bytes = b"",
    returncode: int = 0,
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.CompletedProcess(
        args="",
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )


def _mock_subprocess(
    monkeypatch: pytest.MonkeyPatch,
    result: subprocess.CompletedProcess[bytes],
) -> None:
    fake_module = types.SimpleNamespace(
        run=lambda *a, **kw: result,
        TimeoutExpired=subprocess.TimeoutExpired,
    )
    monkeypatch.setattr("ralph.mcp.tools.unsafe_exec.subprocess", fake_module)


class TestUnsafeExecCapabilityGate:
    def test_requires_process_exec_unbounded_capability(self, tmp_path: Path) -> None:
        session = MockSession(set())
        workspace = MockWorkspaceRoot(tmp_path)
        with pytest.raises(CapabilityDeniedError):
            handle_unsafe_exec(session, workspace, {"command": "ls"})

    def test_rejects_empty_command(self, tmp_path: Path) -> None:
        session = MockSession({PROCESS_EXEC_UNBOUNDED_CAPABILITY})
        workspace = MockWorkspaceRoot(tmp_path)
        with pytest.raises(InvalidParamsError):
            handle_unsafe_exec(session, workspace, {"command": ""})

    def test_rejects_whitespace_only_command(self, tmp_path: Path) -> None:
        session = MockSession({PROCESS_EXEC_UNBOUNDED_CAPABILITY})
        workspace = MockWorkspaceRoot(tmp_path)
        with pytest.raises(InvalidParamsError):
            handle_unsafe_exec(session, workspace, {"command": "   "})

    def test_rejects_missing_command(self, tmp_path: Path) -> None:
        session = MockSession({PROCESS_EXEC_UNBOUNDED_CAPABILITY})
        workspace = MockWorkspaceRoot(tmp_path)
        with pytest.raises(InvalidParamsError):
            handle_unsafe_exec(session, workspace, {})


class TestUnsafeExecVcsBlacklist:
    def test_blocks_git_command(self, tmp_path: Path) -> None:
        session = MockSession({PROCESS_EXEC_UNBOUNDED_CAPABILITY})
        workspace = MockWorkspaceRoot(tmp_path)
        with pytest.raises(CapabilityDeniedError, match="git"):
            handle_unsafe_exec(session, workspace, {"command": "git status"})

    def test_blocks_hg_command(self, tmp_path: Path) -> None:
        session = MockSession({PROCESS_EXEC_UNBOUNDED_CAPABILITY})
        workspace = MockWorkspaceRoot(tmp_path)
        with pytest.raises(CapabilityDeniedError):
            handle_unsafe_exec(session, workspace, {"command": "hg update"})

    def test_blocks_svn_command(self, tmp_path: Path) -> None:
        session = MockSession({PROCESS_EXEC_UNBOUNDED_CAPABILITY})
        workspace = MockWorkspaceRoot(tmp_path)
        with pytest.raises(CapabilityDeniedError):
            handle_unsafe_exec(session, workspace, {"command": "svn commit"})

    def test_blocks_git_case_insensitive(self, tmp_path: Path) -> None:
        session = MockSession({PROCESS_EXEC_UNBOUNDED_CAPABILITY})
        workspace = MockWorkspaceRoot(tmp_path)
        with pytest.raises(CapabilityDeniedError):
            handle_unsafe_exec(session, workspace, {"command": "GIT status"})


class TestUnsafeExecAllowedCommands:
    def test_allows_npm_command(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        session = MockSession({PROCESS_EXEC_UNBOUNDED_CAPABILITY})
        workspace = MockWorkspaceRoot(tmp_path)
        _mock_subprocess(monkeypatch, _make_completed_process(stdout=b"ok", returncode=0))
        result = handle_unsafe_exec(session, workspace, {"command": "npm test"})
        assert result.is_error is False

    def test_allows_make_command(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        session = MockSession({PROCESS_EXEC_UNBOUNDED_CAPABILITY})
        workspace = MockWorkspaceRoot(tmp_path)
        _mock_subprocess(monkeypatch, _make_completed_process(stdout=b"built", returncode=0))
        result = handle_unsafe_exec(session, workspace, {"command": "make build"})
        assert result.is_error is False

    def test_allows_brew_command(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        session = MockSession({PROCESS_EXEC_UNBOUNDED_CAPABILITY})
        workspace = MockWorkspaceRoot(tmp_path)
        _mock_subprocess(monkeypatch, _make_completed_process(returncode=0))
        result = handle_unsafe_exec(session, workspace, {"command": "brew install wget"})
        assert result.is_error is False


class TestUnsafeExecOutput:
    def test_returns_output_text(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        session = MockSession({PROCESS_EXEC_UNBOUNDED_CAPABILITY})
        workspace = MockWorkspaceRoot(tmp_path)
        _mock_subprocess(
            monkeypatch,
            _make_completed_process(
                stdout=b"hello stdout", stderr=b"hello stderr", returncode=0
            ),
        )
        result = handle_unsafe_exec(session, workspace, {"command": "echo hello"})
        content = result.content[0]
        assert isinstance(content, ToolContent)
        assert "Command:" in content.text
        assert "Exit code:" in content.text
        assert "Stdout:" in content.text
        assert "Stderr:" in content.text

    def test_nonzero_exit_sets_is_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        session = MockSession({PROCESS_EXEC_UNBOUNDED_CAPABILITY})
        workspace = MockWorkspaceRoot(tmp_path)
        _mock_subprocess(monkeypatch, _make_completed_process(returncode=1))
        result = handle_unsafe_exec(session, workspace, {"command": "false"})
        assert result.is_error is True

    def test_zero_exit_is_not_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        session = MockSession({PROCESS_EXEC_UNBOUNDED_CAPABILITY})
        workspace = MockWorkspaceRoot(tmp_path)
        _mock_subprocess(monkeypatch, _make_completed_process(returncode=0))
        result = handle_unsafe_exec(session, workspace, {"command": "true"})
        assert result.is_error is False


class TestVcsCommandsConstant:
    def test_git_is_in_vcs_commands(self) -> None:
        assert "git" in _VCS_COMMANDS

    def test_hg_is_in_vcs_commands(self) -> None:
        assert "hg" in _VCS_COMMANDS

    def test_svn_is_in_vcs_commands(self) -> None:
        assert "svn" in _VCS_COMMANDS

    def test_npm_is_not_in_vcs_commands(self) -> None:
        assert "npm" not in _VCS_COMMANDS

    def test_make_is_not_in_vcs_commands(self) -> None:
        assert "make" not in _VCS_COMMANDS

    def test_python_is_not_in_vcs_commands(self) -> None:
        assert "python" not in _VCS_COMMANDS

    def test_ls_is_not_in_vcs_commands(self) -> None:
        assert "ls" not in _VCS_COMMANDS
