"""Tests for ralph/mcp/tool_git_read.py — MCP git read tool handlers."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from ralph.mcp.tool_coordination import CapabilityDeniedError, InvalidParamsError
from ralph.mcp.tool_git_read import (
    _DEFAULT_LOG_COUNT as DEFAULT_LOG_COUNT,
)
from ralph.mcp.tool_git_read import (
    GIT_DIFF_READ_CAPABILITY,
    GIT_STATUS_READ_CAPABILITY,
    ExecutionError,
    WorkspaceWithRoot,
    handle_git_diff,
    handle_git_log,
    handle_git_show,
    handle_git_status,
    parse_git_diff_params,
    parse_git_log_params,
    parse_git_show_params,
    run_git_command,
    run_git_command_lenient,
)

CUSTOM_LOG_COUNT = 20

# =============================================================================
# Mock infrastructure
# =============================================================================


class MockSession:
    """Mock session for capability checks."""

    def __init__(self, capabilities: set[str]) -> None:
        self.session_id = "test-session"
        self._capabilities = capabilities

    def check_capability(self, capability: str) -> object:
        return capability in self._capabilities


class MockWorkspaceRoot:
    """Mock workspace with root property."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root) if isinstance(root, str) else root


# =============================================================================
# parse_git_diff_params tests
# =============================================================================


class TestParseGitDiffParams:
    def test_parses_string_args(self) -> None:
        params = {"args": ["--staged", "--name-only"]}
        result = parse_git_diff_params(params)
        assert result.args == ["--staged", "--name-only"]

    def test_filters_non_string_args(self) -> None:
        params = {"args": ["--staged", 123, None, True, "--name-only"]}
        result = parse_git_diff_params(params)
        assert result.args == ["--staged", "--name-only"]

    def test_empty_args_for_non_list(self) -> None:
        params = {"args": "not a list"}
        result = parse_git_diff_params(params)
        assert result.args == []

    def test_missing_args_returns_empty_list(self) -> None:
        params: dict[str, object] = {}
        result = parse_git_diff_params(params)
        assert result.args == []


# =============================================================================
# parse_git_log_params tests
# =============================================================================


class TestParseGitLogParams:
    def test_parses_count(self) -> None:
        params = {"count": CUSTOM_LOG_COUNT}
        result = parse_git_log_params(params)
        assert result.count == CUSTOM_LOG_COUNT

    def test_defaults_to_10(self) -> None:
        params: dict[str, object] = {}
        result = parse_git_log_params(params)
        assert result.count == DEFAULT_LOG_COUNT

    def test_negative_count_defaults_to_10(self) -> None:
        params = {"count": -5}
        result = parse_git_log_params(params)
        assert result.count == DEFAULT_LOG_COUNT

    def test_non_int_count_defaults_to_10(self) -> None:
        params: dict[str, object] = {"count": "many"}
        result = parse_git_log_params(params)
        assert result.count == DEFAULT_LOG_COUNT


# =============================================================================
# parse_git_show_params tests
# =============================================================================


class TestParseGitShowParams:
    def test_parses_ref(self) -> None:
        params = {"ref": "HEAD~1"}
        result = parse_git_show_params(params)
        assert result.git_ref == "HEAD~1"

    def test_missing_ref_raises(self) -> None:
        params: dict[str, object] = {}
        with pytest.raises(InvalidParamsError):
            parse_git_show_params(params)

    def test_non_string_ref_raises(self) -> None:
        params: dict[str, object] = {"ref": 123}
        with pytest.raises(InvalidParamsError):
            parse_git_show_params(params)


# =============================================================================
# run_git_command tests
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


# =============================================================================
# run_git_command_lenient tests
# =============================================================================


class TestRunGitCommandLenient:
    def test_returns_output_regardless_of_exit_code(self, tmp_path: Path) -> None:
        # Even with a failing command, lenient should return output
        result = run_git_command_lenient(tmp_path, ["--version"])
        assert "git version" in result


# =============================================================================
# handle_git_status tests
# =============================================================================


class TestHandleGitStatus:
    def test_status_requires_capability(self, tmp_path: Path) -> None:
        session = MockSession(set())  # No capabilities
        workspace = MockWorkspaceRoot(tmp_path)

        with pytest.raises(CapabilityDeniedError):
            handle_git_status(session, workspace, {})

    def test_status_returns_output(self, tmp_path: Path) -> None:
        session = MockSession({GIT_STATUS_READ_CAPABILITY})
        workspace = MockWorkspaceRoot(tmp_path)

        with patch("ralph.mcp.tool_git_read.run_git_command") as mock_git:
            mock_git.return_value = "On branch main\nnothing to commit"
            result = handle_git_status(session, workspace, {})
            assert result.is_error is False
            assert "On branch main" in result.content[0].text


# =============================================================================
# handle_git_diff tests
# =============================================================================


class TestHandleGitDiff:
    def test_diff_requires_capability(self, tmp_path: Path) -> None:
        session = MockSession(set())
        workspace = MockWorkspaceRoot(tmp_path)

        with pytest.raises(CapabilityDeniedError):
            handle_git_diff(session, workspace, {"args": []})

    def test_diff_accepts_args(self, tmp_path: Path) -> None:
        session = MockSession({GIT_DIFF_READ_CAPABILITY})
        workspace = MockWorkspaceRoot(tmp_path)

        with patch("ralph.mcp.tool_git_read.run_git_command_lenient") as mock_git:
            mock_git.return_value = "diff --staged content"
            result = handle_git_diff(session, workspace, {"args": ["--staged"]})
            assert result.is_error is False


# =============================================================================
# handle_git_log tests
# =============================================================================


class TestHandleGitLog:
    def test_log_requires_capability(self, tmp_path: Path) -> None:
        session = MockSession(set())
        workspace = MockWorkspaceRoot(tmp_path)

        with pytest.raises(CapabilityDeniedError):
            handle_git_log(session, workspace, {})

    def test_log_accepts_count(self, tmp_path: Path) -> None:
        session = MockSession({GIT_STATUS_READ_CAPABILITY})
        workspace = MockWorkspaceRoot(tmp_path)

        with patch("ralph.mcp.tool_git_read.run_git_command") as mock_git:
            mock_git.return_value = "abc123 commit message"
            result = handle_git_log(session, workspace, {"count": 5})
            assert result.is_error is False


# =============================================================================
# handle_git_show tests
# =============================================================================


class TestHandleGitShow:
    def test_show_requires_capability(self, tmp_path: Path) -> None:
        session = MockSession(set())
        workspace = MockWorkspaceRoot(tmp_path)

        with pytest.raises(CapabilityDeniedError):
            handle_git_show(session, workspace, {"ref": "HEAD"})

    def test_show_requires_ref_param(self, tmp_path: Path) -> None:
        session = MockSession({GIT_STATUS_READ_CAPABILITY})
        workspace = MockWorkspaceRoot(tmp_path)

        with pytest.raises(InvalidParamsError):
            handle_git_show(session, workspace, {})

    def test_show_with_nonexistent_ref_returns_error(self, tmp_path: Path) -> None:
        session = MockSession({GIT_STATUS_READ_CAPABILITY})
        workspace = MockWorkspaceRoot(tmp_path)

        # This should raise ExecutionError since the ref doesn't exist
        with pytest.raises(ExecutionError):
            handle_git_show(session, workspace, {"ref": "DOES_NOT_EXIST_12345"})


# =============================================================================
# WorkspaceWithRoot protocol tests
# =============================================================================


class TestWorkspaceWithRootProtocol:
    def test_path_object_satisfies_protocol(self) -> None:
        ws = MockWorkspaceRoot(Path("/tmp"))
        assert isinstance(ws, WorkspaceWithRoot)
        assert ws.root == Path("/tmp")

    def test_str_root_satisfies_protocol(self) -> None:
        result = run_git_command("/tmp", ["--version"])
        assert "git version" in result
