"""Unit tests for external process execution helpers."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

import pytest

from ralph.executor import ProcessExecutionError, ProcessResult, run_process, run_process_async

if TYPE_CHECKING:
    from pathlib import Path

PYTHON = sys.executable
EXIT_CODE = 3
TIMEOUT_SECONDS = 0.05
SYNC_FAILURE_SCRIPT = (
    f"import sys; print('hello'); print('oops', file=sys.stderr); raise SystemExit({EXIT_CODE})"
)
SYNC_TIMEOUT_SCRIPT = "import sys, time; print('before-timeout'); sys.stdout.flush(); time.sleep(1)"
ASYNC_TIMEOUT_SCRIPT = (
    "import sys, time; print('before-async-timeout'); sys.stdout.flush(); time.sleep(1)"
)


def test_run_process_captures_stdout_stderr_and_exit_code(tmp_path: Path) -> None:
    """Synchronous execution should capture output and preserve exit code."""
    result = run_process(PYTHON, ["-c", SYNC_FAILURE_SCRIPT], cwd=tmp_path)

    assert isinstance(result, ProcessResult)
    assert result.returncode == EXIT_CODE
    assert result.succeeded is False
    assert result.stdout.strip() == "hello"
    assert result.stderr.strip() == "oops"
    assert result.command == (PYTHON, "-c", SYNC_FAILURE_SCRIPT)


@pytest.mark.asyncio
async def test_run_process_async_captures_output(tmp_path: Path) -> None:
    """Async execution should capture output from completed processes."""
    result = await run_process_async(
        PYTHON,
        ["-c", "print('async hello')"],
        cwd=tmp_path,
    )

    assert result.succeeded is True
    assert result.returncode == 0
    assert result.stdout.strip() == "async hello"
    assert result.stderr == ""


def test_run_process_timeout_includes_context(tmp_path: Path) -> None:
    """Timeouts should raise a dedicated execution error with partial output."""
    with pytest.raises(ProcessExecutionError) as excinfo:
        run_process(
            PYTHON,
            ["-c", SYNC_TIMEOUT_SCRIPT],
            cwd=tmp_path,
            timeout=TIMEOUT_SECONDS,
        )

    error = excinfo.value
    assert error.timed_out is True
    assert error.timeout == TIMEOUT_SECONDS
    assert error.command == (PYTHON, "-c", SYNC_TIMEOUT_SCRIPT)
    assert error.stdout.strip() == "before-timeout"
    assert "timed out" in str(error)


@pytest.mark.asyncio
async def test_run_process_async_timeout_includes_context(tmp_path: Path) -> None:
    """Async timeouts should terminate the process and expose partial output."""
    with pytest.raises(ProcessExecutionError) as excinfo:
        await run_process_async(
            PYTHON,
            ["-c", ASYNC_TIMEOUT_SCRIPT],
            cwd=tmp_path,
            timeout=TIMEOUT_SECONDS,
        )

    error = excinfo.value
    assert error.timed_out is True
    assert error.stdout.strip() == "before-async-timeout"


def test_run_process_wraps_missing_command(tmp_path: Path) -> None:
    """OS errors should be wrapped in the executor-specific exception."""
    missing_command = "definitely-not-a-real-command-ralph"

    with pytest.raises(ProcessExecutionError) as excinfo:
        run_process(missing_command, cwd=tmp_path)

    error = excinfo.value
    assert error.timed_out is False
    assert error.command == (missing_command,)
    assert error.__cause__ is not None
