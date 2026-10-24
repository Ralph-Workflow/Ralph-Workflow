"""ProcessManager — single source of truth for every child process Ralph spawns."""

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
    from collections.abc import Callable, Mapping, Sequence


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
_SUPERVISOR_SCRIPT: Final[Path] = Path(__file__).with_name("_supervisor.py")
_SIGKILL: Final[int] = 9
_SIGTERM: Final[int] = 15
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


def _install_pdeathsig(signal_number: int = _SIGKILL) -> None:
    """Linux parent-death signal setup run inside child preexec.

    Sets PR_SET_PDEATHSIG (SIGKILL by default) and closes the fork/prctl race by
    sampling getppid() before and after the prctl call.
    """
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
    """Parent-death setup for a child: git gets SIGTERM, everything else SIGKILL.

    Git removes its lock files from a SIGTERM handler and always exits; SIGKILL
    would leave them behind. Every spawn factory goes through here so the choice
    cannot drift between the sync and async paths.

    The returned partial carries the chosen signal number as its single bound
    argument, which is what the parent-death setup installs in the child.
    """
    death_signal = _SIGTERM if Path(command[0]).name == "git" else _SIGKILL
    return functools.partial(_install_pdeathsig, death_signal)


def _supervisor_invocation(command: Sequence[str]) -> list[str]:
    """Build the ``argv`` that runs the supervisor with ``command``.

    The supervisor is stdlib-only, so it runs as a SCRIPT under ``-I``
    (isolated mode): neither the job's ``cwd`` nor the launcher's
    ``PYTHONPATH`` is put on ``sys.path``. ``-I`` implies ``-E``, so a
    ``python -c "from ralph...import main"`` invocation could only resolve
    ``ralph`` from that interpreter's site-packages -- false for every
    checkout that merely exports ``PYTHONPATH`` (this project's own
    ``Makefile`` does exactly that) or runs against the source tree. The
    supervisor then died of ``ModuleNotFoundError`` before it ever forked,
    leaving ``ProcessRecord.pid`` naming a corpse and the job tree wholly
    uncontained. As a script it imports nothing but the stdlib, and
    ``sys.path[0]`` is its OWN directory -- never the job's cwd -- so an
    exec job that ships its own ``ralph`` package still cannot shadow it.
    Isolation applies only to the supervisor interpreter; the worker still
    receives the full ``env``. ``-u`` (unbuffered stdout/stderr) keeps the
    holder's pipe readable in tests that poll the supervisor's stdout. The
    worker command lands in ``sys.argv[1:]`` so the supervisor's
    ``run(argv)`` sees it cleanly.
    """
    return [
        sys.executable,
        "-I",
        "-u",
        os.fsdecode(_SUPERVISOR_SCRIPT),
        *[os.fsdecode(a) for a in command],
    ]


def _require_executable(command: Sequence[str], opts: SpawnOptions, env: Mapping[str, str]) -> None:
    """Raise ``FileNotFoundError`` like ``Popen`` would for a missing executable.

    The supervisor execs the worker after ``Popen`` has already returned, so
    an unresolvable command would otherwise surface as a silent exit 127
    instead of the ``OSError`` every unsupervised spawn raises.
    """
    program = os.fsdecode(command[0])
    # ``which`` checks a path-qualified program (resolved against the child's
    # cwd, as exec would) directly, and searches the child's PATH otherwise.
    target = str(Path(opts.cwd or ".", program)) if os.sep in program else program
    if shutil.which(target, path=env.get("PATH", os.defpath)) is None:
        raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), program)


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
    on_linux = sys.platform.startswith("linux")
    on_main_thread = threading.current_thread() is threading.main_thread()
    if on_linux and not on_main_thread and not opts.require_direct_parent:
        # Off-main-thread spawn (the production HTTP dispatch path: see
        # ``ralph/mcp/server/_saturated_dispatch.py:155-173`` -> the exec
        # tool's registry.start). Popen with NO ``preexec_fn`` is safe from
        # any thread; the multi-threaded-fork hazard is contained in the
        # supervisor itself, which is a fresh single-threaded interpreter.
        # PG kill from the supervisor's SIGTERM handler cascades to
        # worker + every descendant because ``start_new_session=True`` puts
        # the supervisor at the head of a fresh PG, and the worker inherits
        # that PG at fork time -- covering the documented gap that PR_SET_PDEATHSIG
        # doesn't survive a sigkill + grandchild hop on the legacy path.
        #
        # The predicate keys on the THREAD, never on the label: every
        # off-main-thread Linux spawn needs tree containment, and an
        # ``exec_job:``-only gate silently left every other off-main-thread
        # caller (agent invoke, upstream stdio, git probes, the exec tool's
        # bounded path) unprotected while contradicting
        # ``SpawnOptions.require_direct_parent``'s documented contract --
        # which is exactly the opt-out this branch honours, so a child whose
        # own containment compares ``getppid()`` with its owner (the MCP
        # server's RALPH_MCP_OWNER_PID watch) stays the spawner's DIRECT
        # child instead of naming a supervisor it never spawned.
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
    preexec: Callable[[], None] | None = (
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
    "death_preexec",
    "get_process_manager",
    "process_phase_scope",
    "reset_process_manager",
    "verify_process_liveness",
]
