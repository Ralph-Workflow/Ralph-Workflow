"""Tests for ralph/mcp/tools/unsafe_exec.py -- MCP unsafe_exec tool handler."""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

import pytest

from ralph.mcp.tools._exec_completed_process import _CompletedProcessAdapter
from ralph.mcp.tools.coordination import (
    CapabilityDeniedError,
    InvalidParamsError,
    ToolContent,
)
from ralph.mcp.tools.exec import ExecRunDeps
from ralph.mcp.tools.unsafe_exec import (
    _VCS_COMMANDS,
    PROCESS_EXEC_UNBOUNDED_CAPABILITY,
    handle_unsafe_exec,
)
from ralph.timeout_defaults import EXEC_DEFAULT_TIMEOUT_MS, EXEC_MAX_TIMEOUT_MS
from tests.mock_session import MockSession
from tests.mock_workspace_root import MockWorkspaceRoot

if TYPE_CHECKING:
    from pathlib import Path


def _runner(
    *,
    stdout: bytes = b"",
    stderr: bytes = b"",
    returncode: int = 0,
    truncated: bool = False,
    capture: list[object] | None = None,
) -> ExecRunDeps:
    """Inject a fake bounded runner returning a fixed adapter."""

    def _run(
        _argv: list[str], _cwd: Path, timeout_seconds: float | None
    ) -> _CompletedProcessAdapter:
        if capture is not None:
            capture.append(timeout_seconds)
        return _CompletedProcessAdapter(
            stdout=stdout, stderr=stderr, returncode=returncode, truncated=truncated
        )

    return ExecRunDeps(runner=_run)


def _timeout_deps() -> ExecRunDeps:
    def _run(
        _argv: list[str], _cwd: Path, timeout_seconds: float | None
    ) -> _CompletedProcessAdapter:
        raise subprocess.TimeoutExpired(cmd="x", timeout=timeout_seconds or 1.0)

    return ExecRunDeps(runner=_run)


class TestUnsafeExecAlwaysBounded:
    """timeout_ms<=0 must clamp to the default, never become an unbounded call that
    hangs the MCP server thread; oversized timeouts clamp to the max."""

    def test_zero_timeout_passes_bounded_timeout_to_runner(self, tmp_path: Path) -> None:
        captured: list[object] = []
        handle_unsafe_exec(
            MockSession({PROCESS_EXEC_UNBOUNDED_CAPABILITY}),
            MockWorkspaceRoot(tmp_path),
            {"command": "echo hi", "timeout_ms": 0},
            _runner(stdout=b"ok", capture=captured),
        )
        assert captured == [EXEC_DEFAULT_TIMEOUT_MS / 1000]

    def test_oversized_timeout_is_capped_at_max(self, tmp_path: Path) -> None:
        # An over-large timeout_ms must be capped so unsafe_exec cannot outrun the
        # MCP client request timeout (which would re-trigger -32001).
        captured: list[object] = []
        handle_unsafe_exec(
            MockSession({PROCESS_EXEC_UNBOUNDED_CAPABILITY}),
            MockWorkspaceRoot(tmp_path),
            {"command": "echo hi", "timeout_ms": EXEC_MAX_TIMEOUT_MS * 10},
            _runner(stdout=b"ok", capture=captured),
        )
        assert captured == [EXEC_MAX_TIMEOUT_MS / 1000]


class TestUnsafeExecTimeout:
    """A timeout must become an actionable, non-retryable is_error result — not a
    propagated exception that the bridge turns into a retryable -32603 protocol
    error (the 5-hour retry-storm pathology). The message must teach BOTH meanings
    of a timeout, matching what the tool description promises the agent."""

    def test_timeout_returns_actionable_is_error_result_not_exception(
        self, tmp_path: Path
    ) -> None:
        result = handle_unsafe_exec(
            MockSession({PROCESS_EXEC_UNBOUNDED_CAPABILITY}),
            MockWorkspaceRoot(tmp_path),
            {"command": "sleep 999"},
            _timeout_deps(),
        )

        assert result.is_error is True
        content = result.content[0]
        assert isinstance(content, ToolContent)
        text = content.text.lower()
        assert "timed out" in text
        assert "timeout_ms" in text
        # The second interpretation (command may be genuinely stuck) must be present.
        assert any(word in text for word in ("loop", "stuck", "hang", "deadlock"))


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
    def test_allows_npm_command(self, tmp_path: Path) -> None:
        result = handle_unsafe_exec(
            MockSession({PROCESS_EXEC_UNBOUNDED_CAPABILITY}),
            MockWorkspaceRoot(tmp_path),
            {"command": "npm test"},
            _runner(stdout=b"ok", returncode=0),
        )
        assert result.is_error is False

    def test_allows_make_command(self, tmp_path: Path) -> None:
        result = handle_unsafe_exec(
            MockSession({PROCESS_EXEC_UNBOUNDED_CAPABILITY}),
            MockWorkspaceRoot(tmp_path),
            {"command": "make build"},
            _runner(stdout=b"built", returncode=0),
        )
        assert result.is_error is False

    def test_allows_brew_command(self, tmp_path: Path) -> None:
        result = handle_unsafe_exec(
            MockSession({PROCESS_EXEC_UNBOUNDED_CAPABILITY}),
            MockWorkspaceRoot(tmp_path),
            {"command": "brew install wget"},
            _runner(returncode=0),
        )
        assert result.is_error is False


class TestUnsafeExecOutput:
    def test_returns_output_text(self, tmp_path: Path) -> None:
        result = handle_unsafe_exec(
            MockSession({PROCESS_EXEC_UNBOUNDED_CAPABILITY}),
            MockWorkspaceRoot(tmp_path),
            {"command": "echo hello"},
            _runner(stdout=b"hello stdout", stderr=b"hello stderr", returncode=0),
        )
        content = result.content[0]
        assert isinstance(content, ToolContent)
        assert "Command:" in content.text
        assert "Exit code:" in content.text
        assert "Stdout:" in content.text
        assert "Stderr:" in content.text

    def test_nonzero_exit_sets_is_error(self, tmp_path: Path) -> None:
        result = handle_unsafe_exec(
            MockSession({PROCESS_EXEC_UNBOUNDED_CAPABILITY}),
            MockWorkspaceRoot(tmp_path),
            {"command": "false"},
            _runner(returncode=1),
        )
        assert result.is_error is True

    def test_zero_exit_is_not_error(self, tmp_path: Path) -> None:
        result = handle_unsafe_exec(
            MockSession({PROCESS_EXEC_UNBOUNDED_CAPABILITY}),
            MockWorkspaceRoot(tmp_path),
            {"command": "true"},
            _runner(returncode=0),
        )
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


def test_unsafe_exec_large_output_spills_to_file(tmp_path: Path) -> None:
    spill_dir = tmp_path / "spill"
    spill_dir.mkdir()
    body = "".join(f"line-{i:08d}\n" for i in range(150_000)).encode()

    def _run(_argv: list[str], _cwd: Path, _timeout: float | None) -> _CompletedProcessAdapter:
        return _CompletedProcessAdapter(stdout=body, stderr=b"", returncode=0)

    result = handle_unsafe_exec(
        MockSession({PROCESS_EXEC_UNBOUNDED_CAPABILITY}),
        MockWorkspaceRoot(tmp_path),
        {"command": "echo big"},
        ExecRunDeps(runner=_run, spill_dir=spill_dir),
    )

    assert result.is_error is False
    content = result.content[0]
    assert isinstance(content, ToolContent)
    # Bounded preview, not the whole body dumped inline.
    assert len(content.text) < len(body)

    spill_files = list(spill_dir.iterdir())
    assert len(spill_files) == 1
    spilled = spill_files[0]
    assert str(spilled) in content.text
    contents = spilled.read_text()
    assert "line-00000000" in contents
    assert "line-00149999" in contents


def test_unsafe_exec_runs_via_bounded_sh_path_and_spills_truncated(tmp_path: Path) -> None:
    # unsafe_exec must execute through the SAME bounded process-manager path as
    # exec (shell via `sh -c`, output-capped, process-tree killed) so it cannot
    # capture unbounded output (OOM) — and an over-cap, truncated result spills to
    # a file instead of being dumped or discarded.
    spill_dir = tmp_path / "spill"
    spill_dir.mkdir()
    seen_argv: list[list[str]] = []
    big = "".join(f"line-{i:08d}\n" for i in range(150_000)).encode()

    def _run(argv: list[str], _cwd: Path, _timeout: float | None) -> _CompletedProcessAdapter:
        seen_argv.append(argv)
        return _CompletedProcessAdapter(stdout=big, stderr=b"", returncode=-9, truncated=True)

    result = handle_unsafe_exec(
        MockSession({PROCESS_EXEC_UNBOUNDED_CAPABILITY}),
        MockWorkspaceRoot(tmp_path),
        {"command": "yes"},
        ExecRunDeps(runner=_run, spill_dir=spill_dir),
    )

    assert seen_argv == [["sh", "-c", "yes"]]
    content = result.content[0]
    assert isinstance(content, ToolContent)
    spill_files = list(spill_dir.iterdir())
    assert len(spill_files) == 1
    assert str(spill_files[0]) in content.text
    assert "truncated at the capture cap" in content.text
