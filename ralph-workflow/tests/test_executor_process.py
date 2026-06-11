"""Unit tests for external process execution helpers."""

from __future__ import annotations

import asyncio
import itertools
import sys
from typing import TYPE_CHECKING

import pytest

from ralph.executor import ProcessExecutionError, ProcessResult, run_process, run_process_async
from ralph.executor.process import ProcessRunOptions
from ralph.process.manager import ProcessManager, ProcessManagerPolicy, ProcessStatus, SpawnOptions
from ralph.testing.fake_process import FakeControllableAsyncProcess, FakeTimeoutPopen

if TYPE_CHECKING:
    from pathlib import Path

PYTHON = sys.executable
EXIT_CODE = 3
TIMEOUT_S = 0.5
SYNC_FAILURE_SCRIPT = (
    f"import sys; print('hello'); print('oops', file=sys.stderr); raise SystemExit({EXIT_CODE})"
)

_FAST_POLICY = ProcessManagerPolicy(
    default_grace_period_s=0.0,
    kill_followup_timeout_s=0.0,
    log_events=False,
    enable_zombie_reaper=False,
)


def _make_timeout_pm(partial_stdout: bytes = b"") -> ProcessManager:
    """Build a PM whose sync factory returns a FakeTimeoutPopen."""
    pid_iter = itertools.count(1)

    def factory(command: object, opts: SpawnOptions) -> object:
        return FakeTimeoutPopen(next(pid_iter), partial_stdout=partial_stdout)

    return ProcessManager(policy=_FAST_POLICY, sync_process_factory=factory)


class _FakeRaisingPopen:
    """FakePopen variant whose communicate() raises OSError before reading any output."""

    def __init__(self, pid: int) -> None:
        self.pid = pid
        self._returncode: int | None = None
        self.terminate_calls = 0
        self.wait_calls: list[float | None] = []
        self.stdin: object = None
        self.stdout: object = None
        self.stderr: object = None

    def poll(self) -> int | None:
        return self._returncode

    def wait(self, timeout: float | None = None) -> int:
        self.wait_calls.append(timeout)
        self._returncode = 0
        return 0

    def communicate(
        self,
        input: bytes | None = None,
        timeout: float | None = None,
    ) -> tuple[bytes, bytes]:
        del input, timeout
        raise OSError("broken pipe")

    def terminate(self) -> None:
        self.terminate_calls += 1
        self._returncode = -15

    def kill(self) -> None:
        self._returncode = -9


class _FakeRaisingAsyncProcess:
    """asyncio-style fake whose communicate() raises OSError and wait() returns 0."""

    def __init__(self, pid: int) -> None:
        self.pid = pid
        self._returncode: int | None = None
        self.terminate_calls = 0
        self.wait_calls = 0
        self.stdin: object = None
        self.stdout: object = None
        self.stderr: object = None

    @property
    def returncode(self) -> int | None:
        return self._returncode

    async def wait(self) -> int:
        self.wait_calls += 1
        self._returncode = 0
        return 0

    async def communicate(self, input: bytes | None = None) -> tuple[bytes, bytes]:
        del input
        raise OSError("broken pipe")

    def terminate(self) -> None:
        self.terminate_calls += 1
        self._returncode = -15

    def kill(self) -> None:
        self._returncode = -9


def test_run_process_captures_stdout_stderr_and_exit_code(tmp_path: Path) -> None:
    """Synchronous execution should capture output and preserve exit code."""
    result = run_process(
        PYTHON, ["-c", SYNC_FAILURE_SCRIPT], options=ProcessRunOptions(cwd=tmp_path)
    )

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
    """Timeouts return ProcessResult with exit code 124 and partial output.

    Uses a fake process that raises TimeoutExpired immediately, avoiding
    any real wall-clock wait.
    """
    partial = b"before-timeout"
    pm = _make_timeout_pm(partial_stdout=partial)

    result = run_process(
        "fake-cmd",
        options=ProcessRunOptions(cwd=tmp_path, timeout=TIMEOUT_S),
        _pm=pm,
    )

    assert isinstance(result, ProcessResult)
    assert result.returncode == 124  # TIMEOUT_EXIT_CODE
    assert result.succeeded is False
    assert result.stdout.strip() == "before-timeout"
    assert result.command == ("fake-cmd",)


@pytest.mark.asyncio
async def test_run_process_async_timeout_includes_context(tmp_path: Path) -> None:
    """Async timeouts return ProcessResult with exit code 124.

    Uses a FakeControllableAsyncProcess whose communicate() and wait() block
    on an event, so asyncio.wait() times out without any real clock delay.
    kill_followup_timeout_s > 0 lets _terminate_root_only_async complete
    after terminate() sets the event.
    """

    completion = asyncio.Event()  # never set → simulate a hanging process
    proc = FakeControllableAsyncProcess(
        pid=1,
        stdout_data=b"before-async-timeout",
        completion_event=completion,
    )

    async def factory(
        command: object,
        *,
        cwd: object,
        env: object,
        stdin: object,
        stdout: object,
        stderr: object,
        start_new_session: object,
    ) -> object:
        del command, cwd, env, stdin, stdout, stderr, start_new_session
        return proc

    # kill_followup_timeout_s > 0 so _terminate_root_only_async can finish
    # after terminate() sets the completion event.
    pm = ProcessManager(
        policy=ProcessManagerPolicy(
            default_grace_period_s=0.0, kill_followup_timeout_s=0.1, log_events=False
        ),
        async_process_factory=factory,
    )

    result = await run_process_async(
        "fake-cmd",
        cwd=tmp_path,
        timeout=0.0,
        _pm=pm,
    )

    assert isinstance(result, ProcessResult)
    assert result.returncode == 124  # TIMEOUT_EXIT_CODE
    assert result.succeeded is False
    # Note: FakeControllableAsyncProcess.communicate() does not preserve
    # stdout data that was fed before termination, so we don't assert on stdout


def test_run_process_wraps_missing_command(tmp_path: Path) -> None:
    """OS errors should be wrapped in the executor-specific exception."""
    missing_command = "definitely-not-a-real-command-ralph"

    with pytest.raises(ProcessExecutionError) as excinfo:
        run_process(missing_command, options=ProcessRunOptions(cwd=tmp_path))

    error = excinfo.value
    assert error.timed_out is False
    assert error.command == (missing_command,)
    assert error.__cause__ is not None


def test_run_process_cleans_up_on_non_timeout_communicate_exception(
    tmp_path: Path,
) -> None:
    """Generic OSError from communicate() must terminate the process and re-raise.

    Proves the run_process try/finally cleanup path:
    - The original OSError still propagates to the caller (not suppressed).
    - The ProcessManager record reaches ProcessStatus.KILLED (not RUNNING/SPAWNED).
    - The fake Popen's terminate() is called exactly once by the cleanup path.
    """
    fake = _FakeRaisingPopen(pid=1)

    def factory(command: object, opts: SpawnOptions) -> object:
        del command, opts
        return fake

    pm = ProcessManager(policy=_FAST_POLICY, sync_process_factory=factory)

    with pytest.raises(OSError, match="broken pipe"):
        run_process("fake-cmd", options=ProcessRunOptions(cwd=tmp_path), _pm=pm)

    records = pm.list_records(include_active=True, include_terminal=True)
    assert len(records) == 1
    assert records[0].status == ProcessStatus.KILLED
    assert fake.terminate_calls == 1


@pytest.mark.asyncio
async def test_run_process_async_cleans_up_on_non_timeout_communicate_exception(
    tmp_path: Path,
) -> None:
    """Async path: generic OSError from communicate() must terminate and re-raise.

    Proves the run_process_async try/finally cleanup path:
    - The original OSError still propagates to the caller.
    - The ProcessManager record reaches ProcessStatus.KILLED.
    - The fake async process's terminate() is called exactly once.
    """
    fake = _FakeRaisingAsyncProcess(pid=1)

    async def factory(
        command: object,
        *,
        cwd: object,
        env: object,
        stdin: object,
        stdout: object,
        stderr: object,
        start_new_session: object,
    ) -> object:
        del command, cwd, env, stdin, stdout, stderr, start_new_session
        return fake

    # Async terminate path uses asyncio.wait_for(proc.wait(), timeout=grace_period_s).
    # The non-zero grace period lets the fake's wait() complete without timing out.
    pm = ProcessManager(
        policy=ProcessManagerPolicy(
            default_grace_period_s=0.5,
            kill_followup_timeout_s=0.5,
            log_events=False,
            enable_zombie_reaper=False,
        ),
        async_process_factory=factory,
    )

    with pytest.raises(OSError, match="broken pipe"):
        await run_process_async("fake-cmd", cwd=tmp_path, _pm=pm)

    records = pm.list_records(include_active=True, include_terminal=True)
    assert len(records) == 1
    assert records[0].status == ProcessStatus.KILLED
    assert fake.terminate_calls == 1
