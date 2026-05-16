"""Helpers for executing external processes with captured output."""

from __future__ import annotations

import asyncio
import os
import subprocess
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ralph.process.manager import ProcessManager, SpawnOptions, get_process_manager

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path


@dataclass(frozen=True)
class ProcessResult:
    """Captured result from a completed process."""

    command: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str

    @property
    def succeeded(self) -> bool:
        """Return ``True`` when the process exited successfully."""
        return self.returncode == 0


@dataclass(frozen=True)
class ProcessRunOptions:
    """Execution options for run_process and run_process_async."""

    cwd: str | Path | None = None
    env: Mapping[str, str] | None = None
    timeout: float | None = None
    capture_output: bool = True


@dataclass(frozen=True)
class ProcessErrorDetails:
    """Structured error details captured from a failed process launch."""

    timed_out: bool = False
    timeout: float | None = None
    stdout: str = ""
    stderr: str = ""


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
    communicate_task = asyncio.create_task(handle.communicate())

    done, _pending = await asyncio.wait({communicate_task}, timeout=timeout)
    if communicate_task not in done:
        await handle.terminate(grace_period_s=0)
        stdout_bytes, stderr_bytes = await communicate_task
        raise ProcessExecutionError.from_timeout(
            cmd,
            timeout=timeout,
            stdout=_decode_output(stdout_bytes),
            stderr=_decode_output(stderr_bytes),
        )

    stdout_bytes, stderr_bytes = communicate_task.result()

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
        stdout_bytes, stderr_bytes = handle.communicate()
        raise ProcessExecutionError.from_timeout(
            cmd,
            timeout=effective_options.timeout,
            stdout=_decode_output(stdout_bytes),
            stderr=_decode_output(stderr_bytes),
        ) from None

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
