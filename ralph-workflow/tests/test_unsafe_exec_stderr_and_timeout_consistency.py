"""Regression coverage for exec-family result semantics."""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

import ralph.mcp.tools._exec_completed_process as exec_completed_process
from ralph.mcp.tools.coordination import ToolContent
from ralph.mcp.tools.exec import (
    PROCESS_EXEC_BOUNDED_CAPABILITY,
    ExecRunDeps,
    handle_exec_command,
    parse_exec_params,
)
from ralph.mcp.tools.unsafe_exec import (
    PROCESS_EXEC_UNBOUNDED_CAPABILITY,
    handle_unsafe_exec,
)
from ralph.timeout_defaults import EXEC_DEFAULT_TIMEOUT_MS, EXEC_MAX_TIMEOUT_MS
from tests.mock_session import MockSession
from tests.mock_workspace_root import MockWorkspaceRoot

if TYPE_CHECKING:
    from pathlib import Path


def _result_runner(
    *, stdout: bytes = b"", stderr: bytes = b"", returncode: int = 0
) -> ExecRunDeps:
    def run(
        _argv: list[str], _cwd: Path, _timeout: float | None
    ) -> exec_completed_process._CompletedProcessAdapter:
        return exec_completed_process._CompletedProcessAdapter(
            stdout=stdout, stderr=stderr, returncode=returncode
        )

    return ExecRunDeps(runner=run)


def _timeout_runner() -> ExecRunDeps:
    def run(
        _argv: list[str], _cwd: Path, timeout: float | None
    ) -> exec_completed_process._CompletedProcessAdapter:
        raise subprocess.TimeoutExpired(
            cmd="slow", timeout=timeout or 1.0, output=b"progress-line", stderr=b"warning-line"
        )

    return ExecRunDeps(runner=run)


def test_zero_exit_with_stderr_is_success_for_exec_and_unsafe_exec(tmp_path: Path) -> None:
    workspace = MockWorkspaceRoot(tmp_path)
    exec_result = handle_exec_command(
        MockSession({PROCESS_EXEC_BOUNDED_CAPABILITY}),
        workspace,
        {"command": "echo"},
        deps=_result_runner(stderr=b"warning"),
    )
    unsafe_result = handle_unsafe_exec(
        MockSession({PROCESS_EXEC_UNBOUNDED_CAPABILITY}),
        workspace,
        {"command": "echo okay"},
        deps=_result_runner(stderr=b"warning"),
    )

    assert exec_result.is_error is False
    assert unsafe_result.is_error is False


def test_nonzero_exit_without_stderr_is_error_for_exec_and_unsafe_exec(tmp_path: Path) -> None:
    workspace = MockWorkspaceRoot(tmp_path)
    exec_result = handle_exec_command(
        MockSession({PROCESS_EXEC_BOUNDED_CAPABILITY}),
        workspace,
        {"command": "false"},
        deps=_result_runner(returncode=1),
    )
    unsafe_result = handle_unsafe_exec(
        MockSession({PROCESS_EXEC_UNBOUNDED_CAPABILITY}),
        workspace,
        {"command": "false"},
        deps=_result_runner(returncode=1),
    )

    assert exec_result.is_error is True
    assert unsafe_result.is_error is True


def test_timeout_is_error_with_guidance_and_partial_output_for_exec_family(tmp_path: Path) -> None:
    workspace = MockWorkspaceRoot(tmp_path)
    exec_result = handle_exec_command(
        MockSession({PROCESS_EXEC_BOUNDED_CAPABILITY}),
        workspace,
        {"command": "slow", "timeout_ms": 5000},
        deps=_timeout_runner(),
    )
    unsafe_result = handle_unsafe_exec(
        MockSession({PROCESS_EXEC_UNBOUNDED_CAPABILITY}),
        workspace,
        {"command": "slow", "timeout_ms": 5000},
        deps=_timeout_runner(),
    )

    for result in (exec_result, unsafe_result):
        assert result.is_error is True
        content = result.content[0]
        assert isinstance(content, ToolContent)
        assert "progress-line" in content.text
        assert "warning-line" in content.text
        assert "10000" in content.text


def test_exec_timeout_parsing_clamps_invalid_and_oversized_values() -> None:
    assert parse_exec_params({"command": "echo", "timeout_ms": 0}).timeout_ms == EXEC_DEFAULT_TIMEOUT_MS
    assert parse_exec_params({"command": "echo", "timeout_ms": -1}).timeout_ms == EXEC_DEFAULT_TIMEOUT_MS
    assert parse_exec_params({"command": "echo", "timeout_ms": "bad"}).timeout_ms == EXEC_DEFAULT_TIMEOUT_MS
    assert (
        parse_exec_params({"command": "echo", "timeout_ms": EXEC_MAX_TIMEOUT_MS + 1}).timeout_ms
        == EXEC_MAX_TIMEOUT_MS
    )


def test_unsafe_exec_regression_timeout_parsing_clamps_invalid_and_oversized_values(
    tmp_path: Path,
) -> None:
    forwarded_timeouts: list[float | None] = []

    def run(
        _argv: list[str], _cwd: Path, timeout: float | None
    ) -> exec_completed_process._CompletedProcessAdapter:
        forwarded_timeouts.append(timeout)
        return exec_completed_process._CompletedProcessAdapter(stdout=b"", stderr=b"", returncode=0)

    session = MockSession({PROCESS_EXEC_UNBOUNDED_CAPABILITY})
    workspace = MockWorkspaceRoot(tmp_path)
    cases = (
        (0, EXEC_DEFAULT_TIMEOUT_MS),
        (-1, EXEC_DEFAULT_TIMEOUT_MS),
        ("bad", EXEC_DEFAULT_TIMEOUT_MS),
        (EXEC_MAX_TIMEOUT_MS + 1, EXEC_MAX_TIMEOUT_MS),
    )

    for timeout_ms, expected_timeout_ms in cases:
        result = handle_unsafe_exec(
            session,
            workspace,
            {"command": "echo okay", "timeout_ms": timeout_ms},
            deps=ExecRunDeps(runner=run),
        )

        assert result.is_error is False
        assert forwarded_timeouts[-1] == expected_timeout_ms / 1000
