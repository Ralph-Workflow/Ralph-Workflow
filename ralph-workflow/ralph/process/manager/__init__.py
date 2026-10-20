"""ProcessManager — single source of truth for every child process Ralph spawns."""

from __future__ import annotations

import asyncio
import ctypes
import os
import subprocess
import sys
import threading
from typing import TYPE_CHECKING, Final, Protocol, cast

from ralph.process._spawn_env import child_env_for_spawn
from ralph.process.manager._deadline_timeout_expired import DeadlineTimeoutExpired
from ralph.process.manager._managed_async_process import ManagedAsyncProcess
from ralph.process.manager._managed_process import ManagedProcess
from ralph.process.manager._managed_pty_process import ManagedPtyProcess
from ralph.process.manager._process_event import ProcessEvent
from ralph.process.manager._process_liveness import LivenessResult, verify_process_liveness
from ralph.process.manager._process_manager import ProcessManager
from ralph.process.manager._process_manager_policy import ProcessManagerPolicy
from ralph.process.manager._process_manager_types import (
    _AsyncProcessLike,
    _PtyProcessLike,
    _set_defaults,
    _SyncProcessLike,
)
from ralph.process.manager._process_record import ProcessRecord
from ralph.process.manager._process_status import ProcessStatus
from ralph.process.manager._process_termination_error import ProcessTerminationError
from ralph.process.manager._pty_spawn_options import PtySpawnOptions
from ralph.process.manager._singleton import (
    _pm_state,
    get_process_manager,
    process_phase_scope,
    reset_process_manager,
)
from ralph.process.manager._spawn_options import SpawnOptions
from ralph.process.pty import spawn_pty_process

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence


class _Prctl(Protocol):
    def __call__(self, option: int, value: int) -> int: ...


class _PopenConstructor(Protocol):
    def __call__(
        self,
        args: Sequence[str],
        bufsize: int = ...,
        executable: str | None = ...,
        stdin: int | None = ...,
        stdout: int | None = ...,
        stderr: int | None = ...,
        preexec_fn: Callable[[], None] | None = ...,
        close_fds: bool = ...,
        shell: bool = ...,
        cwd: str | None = ...,
        env: dict[str, str] | None = ...,
        universal_newlines: bool | None = ...,
        startupinfo: object = ...,
        creationflags: int = ...,
        restore_signals: bool = ...,
        start_new_session: bool = ...,
        pass_fds: tuple[int, ...] = ...,
        *,
        text: bool | None = ...,
        errors: str | None = ...,
    ) -> subprocess.Popen[bytes]: ...


_PR_SET_PDEATHSIG: Final[int] = 1
_SIGKILL: Final[int] = 9
_PRCTL_FN: _Prctl | None = None
if sys.platform.startswith("linux"):
    try:
        _libc = ctypes.CDLL(None, use_errno=True)
        _raw_prctl: object = getattr(_libc, "prctl", None)
        if callable(_raw_prctl):
            _PRCTL_FN = cast(
                "_Prctl", _raw_prctl
            )  # cast-policy: seam: structural boundary (sqlite Row / lazy module attr / protocol conferee)
    except Exception:
        _PRCTL_FN = None


def _install_pdeathsig() -> None:
    """Linux parent-death signal setup run inside child preexec.

    Sets PR_SET_PDEATHSIG to SIGKILL and closes the fork/prctl race by
    sampling getppid() before and after the prctl call.
    """
    if _PRCTL_FN is None:
        return
    parent_before = os.getppid()
    if parent_before == 1:
        os._exit(0)
    try:
        rc = _PRCTL_FN(_PR_SET_PDEATHSIG, _SIGKILL)
        if rc != 0:
            os._exit(127)
    except Exception:
        return
    if os.getppid() != parent_before:
        os._exit(0)


def _default_sync_process_factory(
    command: Sequence[str],
    opts: SpawnOptions,
) -> _SyncProcessLike:
    # Process spawned via ProcessManager — see ralph.process.manager.ProcessManager
    child_env = child_env_for_spawn(
        opts.env,
        allow_activity_relay_controls=opts.allow_activity_relay_controls,
        allow_broker_secret=opts.allow_broker_secret,
        cwd=opts.cwd,
    )
    preexec: Callable[[], None] | None = (
        _install_pdeathsig
        if sys.platform.startswith("linux")
        and threading.current_thread() is threading.main_thread()
        else None
    )
    popen: _PopenConstructor = subprocess.Popen
    return popen(
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
        preexec_fn=preexec,
    )


#: Per-stream buffer for an agent subprocess's stdout/stderr.
#:
#: ``asyncio``'s default is 64 KiB, and ``readline()`` RAISES
#: ``ValueError`` when a line exceeds it -- then CLEARS the buffer, so
#: the oversized frame and everything queued behind it are lost and the
#: reading loop dies with an error that names none of that. Agent wire
#: frames routinely pass that: measured captures hold single lines of
#: 503 KB (codex), 693 KB (claude) and 24 MB (pi), because one JSON frame
#: carries a whole tool result.
#:
#: Sized to admit those frames while staying well under the raw log's own
#: 50 MB ceiling.
#:
#: Memory: this is a high-water mark, not an allocation -- the buffer
#: grows only with data actually queued. ``asyncio`` pauses the transport
#: at twice the limit, so the worst case is 64 MiB per stream, per
#: concurrently-reading unit, and only while a single frame that large is
#: mid-flight. Lower it if fan-out width times that ceiling matters more
#: than admitting the largest frames measured.
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
        _install_pdeathsig
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

__all__ = [
    "DeadlineTimeoutExpired",
    "LivenessResult",
    "ManagedAsyncProcess",
    "ManagedProcess",
    "ManagedPtyProcess",
    "ProcessEvent",
    "ProcessManager",
    "ProcessManagerPolicy",
    "ProcessRecord",
    "ProcessStatus",
    "ProcessTerminationError",
    "PtySpawnOptions",
    "SpawnOptions",
    "_pm_state",
    "get_process_manager",
    "process_phase_scope",
    "reset_process_manager",
    "verify_process_liveness",
]
