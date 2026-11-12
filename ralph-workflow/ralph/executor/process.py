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
from ralph.process.teardown import teardown_subtree, verified_child_session_pgid

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path

TIMEOUT_EXIT_CODE = 124

_DEFAULT_RUN_PROCESS_LABEL: Final[str] = "executor:run-process"


def _reap_subtree(pid: int | None, pgid: int | None = None, *, label: str) -> None:
    if pid is None:
        return
    with contextlib.suppress(Exception):
        teardown_subtree(pid, issuer=label, pgid=pgid)


def _verified_pgid(pid: int | None) -> int | None:
    if pid is None:
        return None
    try:
        return verified_child_session_pgid(pid)
    except Exception:
        return None


_POST_TERMINATE_DRAIN_SECONDS: Final[float] = 5.0


class ProcessExecutionError(RuntimeError):
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
        return cls(command, f"Failed to execute '{command[0]}': {error}")


async def run_process_async(
    command: str,
    args: Sequence[str] = (),
    *,
    cwd: str | Path | None = None,
    env: Mapping[str, str] | None = None,
    timeout: float | None = None,
    label: str | None = None,
    _pm: ProcessManager | None = None,
) -> ProcessResult:
    cmd = _normalize_command(command, args)
    pm = _pm if _pm is not None else get_process_manager()
    effective_label = label if label is not None else _DEFAULT_RUN_PROCESS_LABEL

    try:
        handle = await pm.spawn_async(
            cmd,
            SpawnOptions(
                cwd=_normalize_cwd(cwd),
                env=_build_env(env),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                label=effective_label,
                start_new_session=True,
            ),
        )
    except OSError as exc:
        raise ProcessExecutionError.from_os_error(cmd, exc) from exc

    communicate_task: asyncio.Task[tuple[bytes, bytes]]
    communicate_task = asyncio.create_task(handle.communicate())

    child_pid = handle.pid
    child_pgid = _verified_pgid(child_pid)
    try:
        done, _pending = await asyncio.wait({communicate_task}, timeout=timeout)
        if communicate_task not in done:
            await handle.terminate(grace_period_s=0)
            _reap_subtree(child_pid, child_pgid, label=effective_label)
            drained, _ = await asyncio.wait(
                {communicate_task}, timeout=_POST_TERMINATE_DRAIN_SECONDS
            )
            if communicate_task in drained:
                stdout_bytes, stderr_bytes = communicate_task.result()
            else:
                communicate_task.cancel()
                stdout_bytes, stderr_bytes = b"", b""
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
                await asyncio.wait_for(handle.wait(), timeout=0)
        raise
    finally:
        _reap_subtree(child_pid, child_pgid, label=effective_label)

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
    effective_options = options or ProcessRunOptions()
    cmd = _normalize_command(command, args)
    pm = _pm if _pm is not None else get_process_manager()
    effective_label = (
        effective_options.label
        if effective_options.label is not None
        else _DEFAULT_RUN_PROCESS_LABEL
    )

    pipe = subprocess.PIPE if effective_options.capture_output else None
    try:
        handle = pm.spawn(
            cmd,
            SpawnOptions(
                cwd=_normalize_cwd(effective_options.cwd),
                env=_build_env(effective_options.env, effective_options.env_remove),
                stdout=pipe,
                stderr=pipe,
                label=effective_label,
                start_new_session=True,
            ),
        )
    except OSError as exc:
        raise ProcessExecutionError.from_os_error(cmd, exc) from exc

    child_pid = handle.pid
    child_pgid = _verified_pgid(child_pid)
    try:
        stdout_bytes, stderr_bytes = handle.communicate(timeout=effective_options.timeout)
    except subprocess.TimeoutExpired as timeout_exc:
        handle.terminate(grace_period_s=0)
        _reap_subtree(child_pid, child_pgid, label=effective_label)
        try:
            stdout_bytes, stderr_bytes = handle.communicate(timeout=_POST_TERMINATE_DRAIN_SECONDS)
        except subprocess.TimeoutExpired as drain_exc:
            stdout_bytes = _partial_output(drain_exc, timeout_exc, "output")
            stderr_bytes = _partial_output(drain_exc, timeout_exc, "stderr")
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
                handle.wait(timeout=0)
        raise
    finally:
        _reap_subtree(child_pid, child_pgid, label=effective_label)

    rc = handle.returncode if handle.returncode is not None else -1
    return ProcessResult(
        command=cmd,
        returncode=rc,
        stdout=_decode_output(stdout_bytes),
        stderr=_decode_output(stderr_bytes),
    )


def _partial_output(
    latest: subprocess.TimeoutExpired, earlier: subprocess.TimeoutExpired, stream: str
) -> bytes:
    for exc in (latest, earlier):
        captured: object = getattr(exc, stream, None)
        if isinstance(captured, bytes) and captured:
            return captured
    return b""


def _normalize_command(command: str, args: Sequence[str]) -> tuple[str, ...]:
    return (command, *args)


def _normalize_cwd(cwd: str | Path | None) -> str | None:
    if cwd is None:
        return None
    return str(cwd)


def _build_env(
    env: Mapping[str, str] | None, remove: frozenset[str] = frozenset()
) -> dict[str, str]:
    merged = dict(os.environ)
    if env is not None:
        merged.update(env)
    for name in remove:
        merged.pop(name, None)
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
