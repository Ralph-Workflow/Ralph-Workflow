from __future__ import annotations

import asyncio
import ctypes
import errno
import functools
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Final, Protocol, cast

from ralph.process._spawn_env import child_env_for_spawn
from ralph.process.manager._deadline_timeout_expired import DeadlineTimeoutExpired
from ralph.process.manager._git_termination import is_git_name
from ralph.process.manager._liveness_assessor import (
    LivenessConfig,
    LivenessVerdict,
    LivenessWindow,
    StallIntegrator,
    SuspicionClock,
    VerdictKind,
    assess,
    awake,
    cpu_busy,
    default_liveness_config,
    evaluate_rules,
    io_active,
    output_recent,
    repetitive,
    step_with_assessment,
    tree_active,
)
from ralph.process.manager._liveness_sampler import (
    _LivenessProc as LivenessProc,
)
from ralph.process.manager._managed_async_process import ManagedAsyncProcess
from ralph.process.manager._managed_process import ManagedProcess
from ralph.process.manager._managed_process_output_limit_exceeded_error import (
    ManagedProcessOutputLimitExceededError,
)
from ralph.process.manager._managed_pty_process import ManagedPtyProcess
from ralph.process.manager._process_event import ProcessEvent
from ralph.process.manager._process_liveness import LivenessResult, verify_process_liveness
from ralph.process.manager._process_manager import ProcessManager
from ralph.process.manager._process_manager_policy import ProcessManagerPolicy
from ralph.process.manager._process_manager_runtime import (
    loguru_event_listener as loguru_event_listener,
)
from ralph.process.manager._process_manager_types import (
    _AsyncProcessLike,
    _PtyProcessLike,
    _set_defaults,
    _SyncProcessLike,
)
from ralph.process.manager._process_manager_types import (
    _AsyncProcessLike as AsyncProcessLike,
)
from ralph.process.manager._process_manager_types import (
    _PsutilModuleLike as PsutilModuleLike,
)
from ralph.process.manager._process_manager_types import (
    _SyncProcessFactory as SyncProcessFactory,
)
from ralph.process.manager._process_manager_types import (
    _SyncProcessLike as SyncProcessLike,
)
from ralph.process.manager._process_record import ProcessRecord
from ralph.process.manager._process_status import ProcessStatus, is_terminal_status
from ralph.process.manager._process_termination_error import ProcessTerminationError
from ralph.process.manager._pty_spawn_options import PtySpawnOptions
from ralph.process.manager._singleton import (
    _pm_state,
    get_process_manager,
    install_process_manager,
    process_phase_scope,
    reset_process_manager,
    shutdown_process_manager,
)
from ralph.process.manager._spawn_options import SpawnOptions
from ralph.process.manager.liveness_verifier import LivenessVerifier
from ralph.process.manager.process_termination_issuer import ProcessTerminationIssuer
from ralph.process.pty import spawn_pty_process

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence


class _Prctl(Protocol):
    def __call__(self, option: int, value: int) -> int: ...


class _PopenConstructor(Protocol):
    def __call__(
        self,
        args: Sequence[str],
        *,
        cwd: str | None = ...,
        env: dict[str, str] | None = ...,
        stdin: int | None = ...,
        stdout: int | None = ...,
        stderr: int | None = ...,
        start_new_session: bool = ...,
        pass_fds: tuple[int, ...] = ...,
        preexec_fn: Callable[[], None] | None = ...,
        text: bool | None = ...,
        errors: str | None = ...,
    ) -> subprocess.Popen[bytes]: ...


_PR_SET_PDEATHSIG: Final[int] = 1
_SUPERVISOR_SCRIPT: Final[Path] = Path(__file__).with_name("_supervisor.py")
_SIGKILL: Final[int] = 9
_SIGTERM: Final[int] = 15
_PRCTL_FN: _Prctl | None = None
if sys.platform.startswith("linux"):
    try:
        _libc = ctypes.CDLL(None, use_errno=True)
        _raw_prctl: object = getattr(_libc, "prctl", None)
        if callable(_raw_prctl):
            _PRCTL_FN = cast("_Prctl", _raw_prctl)
    except Exception:
        _PRCTL_FN = None


def _install_pdeathsig(signal_number: int = _SIGKILL) -> None:
    if _PRCTL_FN is None:
        return
    parent_before = os.getppid()
    if parent_before == 1:
        os._exit(0)
    try:
        rc = _PRCTL_FN(_PR_SET_PDEATHSIG, signal_number)
        if rc != 0:
            os._exit(127)
    except Exception:
        return
    if os.getppid() != parent_before:
        os._exit(0)


def death_preexec(command: Sequence[str]) -> functools.partial[None]:
    death_signal = _SIGTERM if Path(command[0]).name == "git" else _SIGKILL
    return functools.partial(_install_pdeathsig, death_signal)


def _supervisor_invocation(command: Sequence[str]) -> list[str]:
    return [
        sys.executable,
        "-I",
        "-u",
        os.fsdecode(_SUPERVISOR_SCRIPT),
        *[os.fsdecode(a) for a in command],
    ]


def _require_executable(command: Sequence[str], opts: SpawnOptions, env: Mapping[str, str]) -> None:
    program = os.fsdecode(command[0])
    target = str(Path(opts.cwd or ".", program)) if os.sep in program else program
    if shutil.which(target, path=env.get("PATH", os.defpath)) is None:
        raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), program)


def _default_sync_process_factory(
    command: Sequence[str],
    opts: SpawnOptions,
) -> _SyncProcessLike:
    child_env = child_env_for_spawn(
        opts.env,
        allow_activity_relay_controls=opts.allow_activity_relay_controls,
        allow_broker_secret=opts.allow_broker_secret,
        cwd=opts.cwd,
    )
    on_linux = sys.platform.startswith("linux")
    on_main_thread = threading.current_thread() is threading.main_thread()
    if on_linux and not on_main_thread and not opts.require_direct_parent:
        _require_executable(command, opts, child_env)
        popen: _PopenConstructor = subprocess.Popen
        return popen(
            _supervisor_invocation(command),
            cwd=opts.cwd,
            env=child_env,
            stdin=opts.stdin,
            stdout=opts.stdout,
            stderr=opts.stderr,
            start_new_session=True,
            text=opts.text,
            errors=opts.errors if opts.text else None,
            pass_fds=opts.pass_fds,
        )
    preexec_fn: Callable[[], None] | None = (
        death_preexec(command) if on_linux and on_main_thread else None
    )
    popen_ctor: _PopenConstructor = subprocess.Popen
    return popen_ctor(
        command,
        cwd=opts.cwd,
        env=child_env,
        stdin=opts.stdin,
        stdout=opts.stdout,
        stderr=opts.stderr,
        start_new_session=opts.start_new_session,
        text=opts.text,
        errors=opts.errors if opts.text else None,
        pass_fds=opts.pass_fds,
        preexec_fn=preexec_fn,
    )


AGENT_STREAM_BUFFER_BYTES: Final = 32 * 1024 * 1024


async def _default_async_process_factory(
    command: Sequence[str],
    *,
    cwd: str | None,
    env: dict[str, str] | None,
    stdin: int | None,
    stdout: int | None,
    stderr: int | None,
    start_new_session: bool,
) -> _AsyncProcessLike:
    child_env = child_env_for_spawn(env, cwd=cwd)
    preexec: Callable[[], None] | None = (
        death_preexec(command)
        if sys.platform.startswith("linux")
        and threading.current_thread() is threading.main_thread()
        else None
    )
    return await asyncio.create_subprocess_exec(
        *command,
        cwd=cwd,
        env=child_env,
        stdin=stdin,
        stdout=stdout,
        stderr=stderr,
        start_new_session=start_new_session,
        limit=AGENT_STREAM_BUFFER_BYTES,
        preexec_fn=preexec,
    )


def _default_pty_process_factory(
    command: Sequence[str],
    *,
    cwd: str | None,
    env: dict[str, str] | None,
    cols: int,
    rows: int,
) -> _PtyProcessLike:
    return spawn_pty_process(command, cwd=cwd, env=env, cols=cols, rows=rows)


_set_defaults(
    _default_sync_process_factory,
    _default_async_process_factory,
    _default_pty_process_factory,
)

default_sync_process_factory = _default_sync_process_factory

__all__ = [
    "AsyncProcessLike",
    "DeadlineTimeoutExpired",
    "LivenessConfig",
    "LivenessProc",
    "LivenessResult",
    "LivenessVerdict",
    "LivenessVerifier",
    "LivenessWindow",
    "ManagedAsyncProcess",
    "ManagedProcess",
    "ManagedProcessOutputLimitExceededError",
    "ManagedPtyProcess",
    "ProcessEvent",
    "ProcessManager",
    "ProcessManagerPolicy",
    "ProcessRecord",
    "ProcessStatus",
    "ProcessTerminationError",
    "ProcessTerminationIssuer",
    "PsutilModuleLike",
    "PtySpawnOptions",
    "SpawnOptions",
    "StallIntegrator",
    "SuspicionClock",
    "SyncProcessFactory",
    "SyncProcessLike",
    "VerdictKind",
    "_pm_state",
    "assess",
    "awake",
    "cpu_busy",
    "death_preexec",
    "default_liveness_config",
    "default_sync_process_factory",
    "evaluate_rules",
    "get_process_manager",
    "install_process_manager",
    "io_active",
    "is_git_name",
    "is_terminal_status",
    "loguru_event_listener",
    "output_recent",
    "process_phase_scope",
    "repetitive",
    "reset_process_manager",
    "shutdown_process_manager",
    "step_with_assessment",
    "tree_active",
    "verify_process_liveness",
]
