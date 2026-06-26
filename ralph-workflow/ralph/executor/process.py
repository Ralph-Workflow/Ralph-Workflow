"""Helpers for executing external processes with captured output."""

from __future__ import annotations

import asyncio
import contextlib
import os
import subprocess
from typing import TYPE_CHECKING, Final

from ralph.executor._process_error_details import ProcessErrorDetails
from ralph.executor._process_result import ProcessResult
from ralph.executor._process_run_options import ProcessRunOptions
from ralph.process.manager import ProcessManager, SpawnOptions, get_process_manager
from ralph.process.manager._process_status import _TERMINAL_STATUSES

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path

# Standard unix timeout exit code
TIMEOUT_EXIT_CODE = 124

# Defense-in-depth bound for the post-terminate pipe drain. The child has
# already been escalated SIGTERM -> SIGKILL via ``terminate(grace_period_s=0)``,
# so a healthy OS reaps the pipes within milliseconds. This bound only fires
# for a wedged (e.g. uninterruptible D-state) child that ignores SIGKILL; we
# must not hang the caller forever in that pathological case.
_POST_TERMINATE_DRAIN_SECONDS: Final[float] = 5.0


class ProcessExecutionError(RuntimeError):
    """Raised when a process cannot be started or exceeds its timeout."""

    def __init__(
        self,
        command: tuple[str, ...],
        message: str,
        details: ProcessErrorDetails | None = None,
    ) -> None:
        self.command = command
        payload = details or ProcessErrorDetails()
        self.timed_out = payload.timed_out
        self.timeout = payload.timeout
        self.stdout = payload.stdout
        self.stderr = payload.stderr
        super().__init__(message)

    @classmethod
    def from_timeout(
        cls,
        command: tuple[str, ...],
        *,
        timeout: float | None,
        stdout: str,
        stderr: str,
    ) -> ProcessExecutionError:
        """Build a timeout error with captured partial output."""
        executable = command[0]
        message = f"Failed to execute '{executable}': timed out"
        if timeout is not None:
            message = f"{message} after {timeout}s"
        return cls(
            command,
            message,
            ProcessErrorDetails(
                timed_out=True,
                timeout=timeout,
                stdout=stdout,
                stderr=stderr,
            ),
        )

    @classmethod
    def from_os_error(
        cls,
        command: tuple[str, ...],
        error: OSError,
    ) -> ProcessExecutionError:
        """Build an execution error from an OS-level failure."""
        return cls(command, f"Failed to execute '{command[0]}': {error}")


async def run_process_async(
    command: str,
    args: Sequence[str] = (),
    *,
    cwd: str | Path | None = None,
    env: Mapping[str, str] | None = None,
    timeout: float | None = None,
    _pm: ProcessManager | None = None,
) -> ProcessResult:
    """Run a process asynchronously and capture its output."""
    cmd = _normalize_command(command, args)
    pm = _pm if _pm is not None else get_process_manager()

    try:
        handle = await pm.spawn_async(
            cmd,
            SpawnOptions(
                cwd=_normalize_cwd(cwd),
                env=_build_env(env),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            ),
        )
    except OSError as exc:
        raise ProcessExecutionError.from_os_error(cmd, exc) from exc

    communicate_task: asyncio.Task[tuple[bytes, bytes]]
    communicate_task = asyncio.create_task(handle.communicate())  # mcp-timeout-ok: wait-bounded

    try:
        done, _pending = await asyncio.wait({communicate_task}, timeout=timeout)
        if communicate_task not in done:
            await handle.terminate(grace_period_s=0)
            stdout_bytes, stderr_bytes = await communicate_task
            # Return exit code TIMEOUT_EXIT_CODE on timeout (standard unix timeout exit code)
            # instead of raising an exception, so callers can handle it gracefully
            return ProcessResult(
                command=cmd,
                returncode=TIMEOUT_EXIT_CODE,
                stdout=_decode_output(stdout_bytes),
                stderr=_decode_output(stderr_bytes),
            )

        stdout_bytes, stderr_bytes = communicate_task.result()
    except BaseException:
        with contextlib.suppress(Exception):
            communicate_task.cancel()
        if handle.record.status not in _TERMINAL_STATUSES:
            with contextlib.suppress(Exception):
                await handle.terminate(grace_period_s=0)
            with contextlib.suppress(Exception):
                await asyncio.wait_for(handle.wait(), timeout=0)  # mcp-timeout-ok: wait_for-bounded
        raise

    rc = handle.returncode if handle.returncode is not None else -1
    return ProcessResult(
        command=cmd,
        returncode=rc,
        stdout=_decode_output(stdout_bytes),
        stderr=_decode_output(stderr_bytes),
    )


def run_process(
    command: str,
    args: Sequence[str] = (),
    *,
    options: ProcessRunOptions | None = None,
    _pm: ProcessManager | None = None,
) -> ProcessResult:
    """Run a process synchronously, optionally capturing output.

    When ``options.capture_output`` is ``False`` the child process inherits the
    parent's stdout/stderr so output streams directly to the terminal.  The
    returned ``ProcessResult`` will have empty ``stdout`` and ``stderr`` strings.
    """
    effective_options = options or ProcessRunOptions()
    cmd = _normalize_command(command, args)
    pm = _pm if _pm is not None else get_process_manager()

    pipe = subprocess.PIPE if effective_options.capture_output else None
    try:
        handle = pm.spawn(
            cmd,
            SpawnOptions(
                cwd=_normalize_cwd(effective_options.cwd),
                env=_build_env(effective_options.env),
                stdout=pipe,
                stderr=pipe,
            ),
        )
    except OSError as exc:
        raise ProcessExecutionError.from_os_error(cmd, exc) from exc

    try:
        stdout_bytes, stderr_bytes = handle.communicate(timeout=effective_options.timeout)
    except subprocess.TimeoutExpired:
        handle.terminate(grace_period_s=0)
        # Bound the post-terminate drain so a wedged child (one that ignores
        # SIGKILL, e.g. uninterruptible D-state) cannot hang the caller. The
        # child has already been escalated to SIGKILL above, so a healthy OS
        # closes the pipes within milliseconds; the bound only fires in the
        # pathological case where the OS never reaps the child.
        try:
            stdout_bytes, stderr_bytes = handle.communicate(
                timeout=_POST_TERMINATE_DRAIN_SECONDS
            )
        except subprocess.TimeoutExpired:
            stdout_bytes, stderr_bytes = b"", b""
        # Return exit code TIMEOUT_EXIT_CODE on timeout (standard unix timeout exit code)
        # instead of raising an exception, so callers can handle it gracefully
        return ProcessResult(
            command=cmd,
            returncode=TIMEOUT_EXIT_CODE,
            stdout=_decode_output(stdout_bytes),
            stderr=_decode_output(stderr_bytes),
        )
    except BaseException:
        if handle.record.status not in _TERMINAL_STATUSES:
            with contextlib.suppress(Exception):
                handle.terminate(grace_period_s=0)
            with contextlib.suppress(Exception):
                handle.wait(timeout=0)  # mcp-timeout-ok: bounded
        raise

    rc = handle.returncode if handle.returncode is not None else -1
    return ProcessResult(
        command=cmd,
        returncode=rc,
        stdout=_decode_output(stdout_bytes),
        stderr=_decode_output(stderr_bytes),
    )


def _normalize_command(command: str, args: Sequence[str]) -> tuple[str, ...]:
    return (command, *args)


def _normalize_cwd(cwd: str | Path | None) -> str | None:
    if cwd is None:
        return None
    return str(cwd)


def _build_env(env: Mapping[str, str] | None) -> dict[str, str]:
    merged = dict(os.environ)
    if env is not None:
        merged.update(env)
    return merged


def _decode_output(output: str | bytes | None) -> str:
    if output is None:
        return ""
    if isinstance(output, bytes):
        return output.decode("utf-8", errors="replace")
    return output


__all__ = [
    "ProcessExecutionError",
    "ProcessResult",
    "ProcessRunOptions",
    "run_process",
    "run_process_async",
]
