
from __future__ import annotations

import contextlib
import importlib
import os
import subprocess
import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from ralph.git.git_call_policy import GitCallClass, effective_terminate_grace_seconds
from ralph.git.git_run_result import GitRunResult
from ralph.process.manager import SpawnOptions, get_process_manager
from ralph.process.manager._managed_process_output_limit_exceeded_error import (
    ManagedProcessOutputLimitExceededError,
)
from ralph.timeout_defaults import GIT_SUBPROCESS_TIMEOUT_SECONDS

_GIT_BATCH_MODE_ENV: dict[str, str] = {
    "GIT_TERMINAL_PROMPT": "0",
    "GCM_INTERACTIVE": "Never",
    "GIT_EDITOR": ":",
    "GIT_SEQUENCE_EDITOR": ":",
    "GIT_PAGER": "cat",
    "EDITOR": ":",
    "VISUAL": ":",
    "LC_ALL": "C",
    "GIT_NO_REPLACE_OBJECTS": "1",
}

_UNREPORTED_RETURNCODE: int = -9

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path
    from typing import Protocol

    class _QueuedRunGit(Protocol):
        def __call__(
            self,
            args: Sequence[str],
            *,
            cwd: Path | None,
            label: str,
            options: GitRunOptions | None = None,
            call_class: GitCallClass | None = None,
        ) -> GitRunResult: ...

    class _PopenExitProtocol:
        def __exit__(
            self,
            exc_type: object,
            exc: object,
            tb: object,
        ) -> object: ...


_SCRUBBED_GIT_ENV_KEYS: frozenset[str] = frozenset(
    {"GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR"}
)


def _build_spawn_env(
    caller_env: Mapping[str, str] | None,
) -> dict[str, str]:
    merged: dict[str, str] = {}
    for source in (os.environ, _GIT_BATCH_MODE_ENV):
        for key, value in source.items():
            if key in _SCRUBBED_GIT_ENV_KEYS:
                continue
            merged[key] = value
    if caller_env is not None:
        for key, value in caller_env.items():
            if key in _SCRUBBED_GIT_ENV_KEYS:
                continue
            merged[key] = value
    return merged


@dataclass(frozen=True)
class GitRunOptions:

    phase: str | None = None
    timeout: float | None = None
    env: Mapping[str, str] | None = None
    check: bool = False
    capture_output: bool = True
    text: bool = True
    output_limit_bytes: int | None = None
    input_data: str | bytes | None = None
    call_class: GitCallClass | None = None


def run_git(
    args: Sequence[str],
    *,
    cwd: Path | None,
    label: str,
    options: GitRunOptions | None = None,
    call_class: GitCallClass | None = None,
) -> GitRunResult:
    queued_mod = sys.modules.get("ralph.git.git_command_queue")
    if queued_mod is None:
        queued_mod = importlib.import_module("ralph.git.git_command_queue")
    queued_run_git = cast("_QueuedRunGit", queued_mod.route_run_git)

    return queued_run_git(
        args,
        cwd=cwd,
        label=label,
        options=options,
        call_class=call_class,
    )


def _spawn_git(
    args: Sequence[str],
    *,
    cwd: Path | None,
    label: str,
    options: GitRunOptions | None = None,
) -> GitRunResult:
    effective_options = options or GitRunOptions()
    phase = effective_options.phase
    terminate_grace_seconds = effective_terminate_grace_seconds(effective_options.call_class)
    effective_label = f"phase:{phase}:git:{label}" if phase is not None else label
    cmd = ("git", *args)
    effective_timeout = (
        effective_options.timeout
        if effective_options.timeout is not None
        else GIT_SUBPROCESS_TIMEOUT_SECONDS
    )
    input_data = (
        effective_options.input_data
        if isinstance(effective_options.input_data, bytes)
        else effective_options.input_data.encode()
        if isinstance(effective_options.input_data, str)
        else None
    )
    spawn_env = _build_spawn_env(effective_options.env)
    proc = get_process_manager().spawn(
        cmd,
        SpawnOptions(
            cwd=str(cwd) if cwd else None,
            env=spawn_env,
            stdin=subprocess.PIPE
            if effective_options.input_data is not None
            else subprocess.DEVNULL,
            stdout=subprocess.PIPE if effective_options.capture_output else None,
            stderr=subprocess.PIPE if effective_options.capture_output else None,
            label=effective_label,
            text=effective_options.text and input_data is None,
        ),
    )
    try:
        if input_data is not None:
            raw_stdout, raw_stderr = proc.communicate_and_cleanup(
                input=input_data,
                timeout=effective_timeout,
                cleanup_grace_period_s=0.0,
                terminate_grace_period_s=terminate_grace_seconds,
                output_limit_bytes=effective_options.output_limit_bytes,
            )
        else:
            raw_stdout, raw_stderr = proc.communicate_and_cleanup(
                timeout=effective_timeout,
                cleanup_grace_period_s=0.0,
                terminate_grace_period_s=terminate_grace_seconds,
                output_limit_bytes=effective_options.output_limit_bytes,
            )
        with contextlib.suppress(Exception):
            proc.poll()
        with contextlib.suppress(Exception):
            proc.wait(timeout=0)
        with contextlib.suppress(Exception):
            get_process_manager().drain_completed()
    except subprocess.TimeoutExpired:
        proc.terminate(grace_period_s=terminate_grace_seconds)
        raise
    except ManagedProcessOutputLimitExceededError:
        proc.terminate(grace_period_s=terminate_grace_seconds)
        raise
    except BaseException:
        with contextlib.suppress(Exception):
            proc.terminate(grace_period_s=terminate_grace_seconds)
        raise
    finally:
        raw_proc_obj: object = proc._proc
        for stream in (proc.stdout, proc.stderr):
            if stream is None:
                continue
            with contextlib.suppress(Exception):
                stream.close()
        if hasattr(raw_proc_obj, "__exit__"):
            raw_proc = cast(
                "_PopenExitProtocol", raw_proc_obj
            )
            with contextlib.suppress(Exception):
                raw_proc.__exit__(None, None, None)

    def _str(v: bytes | str | None) -> str:
        if v is None:
            return ""
        return v.decode() if isinstance(v, bytes) else v

    stdout = _str(raw_stdout)
    stderr = _str(raw_stderr)
    returncode = proc.returncode if proc.returncode is not None else _UNREPORTED_RETURNCODE

    if effective_options.check and returncode != 0:
        raise subprocess.CalledProcessError(returncode, list(cmd), stdout, stderr)

    return GitRunResult(args=cmd, returncode=returncode, stdout=stdout, stderr=stderr)


__all__ = ["GitRunOptions", "run_git"]


