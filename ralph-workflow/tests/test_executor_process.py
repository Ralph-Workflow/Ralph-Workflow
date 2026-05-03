"""Unit tests for external process execution helpers."""

from __future__ import annotations

import itertools
import sys
from typing import TYPE_CHECKING

import pytest

from ralph.executor import ProcessExecutionError, ProcessResult, run_process, run_process_async
from ralph.process.manager import ProcessManager, ProcessManagerPolicy
from ralph.testing.fake_process import FakeTimeoutPopen

if TYPE_CHECKING:
    from pathlib import Path

PYTHON = sys.executable
EXIT_CODE = 3
TIMEOUT_S = 0.5
SYNC_FAILURE_SCRIPT = (
    f"import sys; print('hello'); print('oops', file=sys.stderr); raise SystemExit({EXIT_CODE})"
)

_FAST_POLICY = ProcessManagerPolicy(
    default_grace_period_s=0.0, kill_followup_timeout_s=0.0, log_events=False
)


def _make_timeout_pm(partial_stdout: bytes = b"") -> ProcessManager:
    """Build a PM whose sync factory returns a FakeTimeoutPopen."""
    pid_iter = itertools.count(1)

    def factory(command, *, cwd, env, stdin, stdout, stderr, start_new_session, text):  # noqa: PLR0913
        return FakeTimeoutPopen(next(pid_iter), partial_stdout=partial_stdout)

    return ProcessManager(policy=_FAST_POLICY, sync_process_factory=factory)


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
    """Timeouts raise a dedicated execution error with partial output.

    Uses a fake process that raises TimeoutExpired immediately, avoiding
    any real wall-clock wait.
    """
    partial = b"before-timeout"
    pm = _make_timeout_pm(partial_stdout=partial)

    with pytest.raises(ProcessExecutionError) as excinfo:
        run_process(
            "fake-cmd",
            cwd=tmp_path,
            timeout=TIMEOUT_S,
            _pm=pm,
        )

    error = excinfo.value
    assert error.timed_out is True
    assert error.timeout == TIMEOUT_S
    assert error.command == ("fake-cmd",)
    assert error.stdout.strip() == "before-timeout"
    assert "timed out" in str(error)


@pytest.mark.asyncio
async def test_run_process_async_timeout_includes_context(tmp_path: Path) -> None:
    """Async timeouts raise a dedicated execution error with partial output.

    Uses a FakeControllableAsyncProcess whose communicate() and wait() block
    on an event, so asyncio.wait() times out without any real clock delay.
    kill_followup_timeout_s > 0 lets _terminate_root_only_async complete
    after terminate() sets the event.
    """
    import asyncio  # noqa: PLC0415

    from ralph.testing.fake_process import FakeControllableAsyncProcess  # noqa: PLC0415

    completion = asyncio.Event()  # never set → simulate a hanging process
    proc = FakeControllableAsyncProcess(
        pid=1,
        stdout_data=b"before-async-timeout",
        completion_event=completion,
    )

    async def factory(command, *, cwd, env, stdin, stdout, stderr, start_new_session):  # noqa: PLR0913
        return proc

    # kill_followup_timeout_s > 0 so _terminate_root_only_async can finish
    # after terminate() sets the completion event.
    pm = ProcessManager(
        policy=ProcessManagerPolicy(
            default_grace_period_s=0.0, kill_followup_timeout_s=0.1, log_events=False
        ),
        async_process_factory=factory,
    )

    with pytest.raises(ProcessExecutionError) as excinfo:
        await run_process_async(
            "fake-cmd",
            cwd=tmp_path,
            timeout=0.0,
            _pm=pm,
        )

    error = excinfo.value
    assert error.timed_out is True
    assert error.timeout == 0.0


def test_run_process_wraps_missing_command(tmp_path: Path) -> None:
    """OS errors should be wrapped in the executor-specific exception."""
    missing_command = "definitely-not-a-real-command-ralph"

    with pytest.raises(ProcessExecutionError) as excinfo:
        run_process(missing_command, cwd=tmp_path)

    error = excinfo.value
    assert error.timed_out is False
    assert error.command == (missing_command,)
    assert error.__cause__ is not None
