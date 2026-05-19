"""ProcessManager — single source of truth for every child process Ralph spawns."""

from __future__ import annotations

import asyncio
import subprocess
from typing import TYPE_CHECKING, cast

from ralph.process.manager._managed_async_process import ManagedAsyncProcess
from ralph.process.manager._managed_process import ManagedProcess
from ralph.process.manager._managed_pty_process import ManagedPtyProcess
from ralph.process.manager._process_event import ProcessEvent
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
    from collections.abc import Sequence


def _default_sync_process_factory(
    command: Sequence[str],
    opts: SpawnOptions,
) -> _SyncProcessLike:
    return cast(
        "subprocess.Popen[bytes]",
        subprocess.Popen(
            command,
            cwd=opts.cwd,
            env=opts.env,
            stdin=opts.stdin,
            stdout=opts.stdout,
            stderr=opts.stderr,
            start_new_session=opts.start_new_session,
            text=opts.text,
        ),
    )


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
    return await asyncio.create_subprocess_exec(
        *command,
        cwd=cwd,
        env=env,
        stdin=stdin,
        stdout=stdout,
        stderr=stderr,
        start_new_session=start_new_session,
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
]
