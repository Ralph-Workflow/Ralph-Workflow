"""Supervisor that adds PR_SET_PDEATHSIG containment to exec jobs spawned off-main-thread.

Background
----------

``ralph.process.manager._default_sync_process_factory`` installs
``PR_SET_PDEATHSIG=SIGKILL`` via ``preexec_fn`` ONLY when the spawn call runs
on Python's main thread. The guard exists because ``preexec_fn`` runs between
``fork`` and ``exec`` in the child, and only the forking thread survives a
multi-threaded fork -- any state the other threads hold (e.g. the GIL,
``_thread.lock`` instances held under the import lock) becomes a corpse that
re-locks the moment the child tries to touch it. Production exec jobs go
through ``ThreadPoolExecutor`` (see ``ralph/mcp/server/_saturated_dispatch.py
:155-173`` -> ``ralph/mcp/tools/exec.py:809-816`` ->
``ralph/mcp/tools/_exec_jobs.py:665-675``); that path runs OFF the main thread,
so ``preexec_fn`` is ``None`` and the exec job is unprotected against holder
SIGKILL. Even when the existing main-thread path fires, PR_SET_PDEATHSIG does
not cascade: a grandchild of the killed exec job survives.

Design
-------

This module is a tiny **supervisor process**. The holder (the MCP server)
spawns the supervisor in place of the real exec job when (and only when):

* ``sys.platform.startswith("linux")`` -- the supervisor relies on
  Linux-only ``PR_SET_PDEATHSIG`` plus ``killpg`` for PG-wide delivery.
* the spawn call is OFF the main thread -- the on-main-thread path keeps
  using the legacy ``preexec_fn`` ``_install_pdeathsig``.
* the spawn did NOT set ``SpawnOptions.require_direct_parent`` -- a child that
  verifies its own parent (``getppid()`` against an owner it was told to
  expect) must stay the spawner's direct child, and a supervisor in between
  would make it report a stranger.

The supervisor is intentionally small and stdlib-only so that
``subprocess.Popen`` (no ``preexec_fn``, safe from a worker thread) can spawn
it cheaply. Everything sensitive to fork-after-multi-thread state happens in
the supervisor's own single-threaded interpreter:

1. ``PR_SET_PDEATHSIG=SIGTERM`` is armed against the supervisor's own parent
   (the MCP server holder). When the holder dies -- whatever signal it died
   from, including SIGKILL -- the kernel delivers SIGTERM to the supervisor.
2. A SIGTERM handler is installed. Once the holder is gone it ``killpg``s
   the supervisor's own PG with SIGKILL and exits; while the holder is alive
   it forwards the signal to the PG instead, so a deliberate terminate keeps
   the holder's grace period and the holder owns escalation. The
   supervisor is spawned with
   ``start_new_session=True`` so it leads its own PG, and the worker is
   ``fork``ed AFTER that point so the worker's PG ID == supervisor's PID. Any
   descendants the worker spawns (e.g. ``sh -c 'sleep 600 & sleep 600'``)
   inherit the worker's PG, so they die in the same ``killpg`` sweep.
3. The forked worker (between ``fork`` and ``exec``) arms
   ``PR_SET_PDEATHSIG=SIGKILL`` against the supervisor so even a supervisor
   killed via SIGKILL (i.e. uncatchable) takes the worker with it.

The supervisor forwards the holder's stdio to the worker by inheritance: the
supervisor inherits its (possibly piped) stdin/stdout/stderr from the
``Popen`` call, and the worker's ``os.execvp`` does not redirect them.

Public surface
--------------

* ``run(argv: list[str]) -> int`` -- the callable that supervises; returns the
  worker's exit status.
* ``main() -> int`` -- the CLI entry point the factory reaches, so the module
  stays importable by the in-repo sweep that proves every spawn-capable entry
  point sanitizes its environment before doing work.
* The factory invokes this file as a script, ``python -I -u <path to
  _supervisor.py> <command...>``: isolated mode keeps the job's ``cwd`` and
  the launcher's ``PYTHONPATH`` off ``sys.path``, which is safe -- and runnable
  at all -- precisely because the module imports nothing but the stdlib. A
  ``python -c "from ralph...import main"`` form cannot make that promise: ``-I``
  implies ``-E``, so ``ralph`` would have to be installed into that
  interpreter's site-packages rather than merely reachable through
  ``PYTHONPATH`` or the source tree, and a checkout that is not installed made
  the supervisor die of ``ModuleNotFoundError`` before it forked anything.
* No module-level side effects on import (no ``atexit``, no signal handlers).
"""

from __future__ import annotations

import contextlib
import ctypes
import os
import signal
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING, Final, NoReturn, Protocol, cast

if TYPE_CHECKING:
    from collections.abc import Callable
    from types import FrameType

# Linux prctl(2) numbers, not exposed by the stdlib.
_PR_SET_PDEATHSIG: Final[int] = 1
_SIGTERM: Final[int] = 15
_SIGKILL: Final[int] = 9
#: How long the group gets to exit on SIGTERM before SIGKILL when the holder is
#: gone. Git removes its ``*.lock`` files from a SIGTERM handler; an immediate
#: SIGKILL orphans them. (Mirrors ``ralph.timeout_defaults.GIT_TERMINATE_GRACE_SECONDS``;
#: this module is stdlib-only and cannot import it.)
_GROUP_TERM_GRACE_S: Final[float] = 5.0
# Copied from ``ralph.process._spawn_env.MALLOC_DEBUG_NOISE_VARS``. This
# module runs under ``python -I``, which drops both the launcher's
# ``PYTHONPATH`` and the job's ``cwd`` from ``sys.path``, so it cannot import
# ANY ``ralph`` module -- not even the one defining that tuple -- in any
# interpreter that was not pip-installed into. Duplicating these two names is
# what keeps the module stdlib-only, and stdlib-only is what makes it runnable
# at all; the canonical scrubber keeps the richer contract for every in-tree
# entry point.
_MALLOC_DEBUG_NOISE_VARS: Final[tuple[str, ...]] = (
    "MallocStackLogging",
    "MallocStackLoggingNoCompact",
)


def sanitize_process_environment() -> tuple[str, ...]:
    """Strip inherited malloc-debug toggles before this process launches the worker.

    Mirrors ``ralph.process._spawn_env.sanitize_process_environment`` for the
    one variable pair that must be gone before ``execvp``. The duplication is
    deliberate and one-directional: that module may grow dependencies, this
    one may not acquire any.
    """
    return tuple(
        name for name in _MALLOC_DEBUG_NOISE_VARS if os.environ.pop(name, None) is not None
    )


class _Prctl(Protocol):
    """Typed view of ``libc.prctl(option, arg2)`` for the structural-boundary cast."""

    def __call__(self, option: int, value: int) -> int: ...


# ctypes cdecl signature for ``int prctl(int option, unsigned long arg2)``.
# The second argument is widened to ``unsigned long`` so any 32/64-bit ABI
# representation of the signal number is accepted. The actual return-type
# annotation comes from the ``_Prctl`` Protocol cast -- mirror of the
# structurally-identical binding at
# ``ralph/process/manager/__init__.py:_PRCTL_FN``. If ``prctl`` is not
# available on this libc (effectively never on Linux, but the bound is
# still tested), ``_prctl`` is replaced by a no-op that always reports
# failure so ``_set_pdeathsig`` returns False without crashing.
_libc = ctypes.CDLL(None, use_errno=True)
_prctl_raw: object = getattr(_libc, "prctl", None)
if callable(_prctl_raw):
    _prctl: _Prctl = cast(
        "_Prctl", _prctl_raw
    )  # cast-policy: seam: structural boundary (sqlite Row / lazy module attr / protocol conferee)
else:

    def _prctl(option: int, value: int) -> int:
        del option, value
        return -1


def _set_pdeathsig(signal_number: int) -> bool:
    """Arm ``PR_SET_PDEATHSIG=signal_number`` against the current parent.

    Returns True on success. Uses the documented before/after ``getppid``
    race-closing pattern: if the parent disappeared between the syscall
    return and the post-call check, the supervisor is already orphaned and
    there is no one to take the signal from.

    The call is made via ``ctypes`` rather than ``python-prctl`` to keep this
    module's dependency surface stdlib-only; a copy exists in
    ``ralph/process/manager/__init__.py:_install_pdeathsig`` (main-thread
    preexec variant) and duplicating it here keeps the two containment
    mechanisms independent. Each is intentionally a few lines -- a
    dependency between them would couple single-thread startup with
    multi-thread spawn, which is exactly what we are trying to keep out.
    """
    parent_before = os.getppid()
    if parent_before <= 1:
        return False
    rc = _prctl(_PR_SET_PDEATHSIG, signal_number)
    if rc != 0:
        return False
    return os.getppid() == parent_before


def _other_group_members_alive(pgid: int) -> bool:
    """Return True while any process other than this one is alive in ``pgid``."""
    me = os.getpid()
    # filesystem-read-ok: /proc is the kernel process-metadata source; this isolated supervisor cannot import the Workspace seam
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit() or int(entry.name) == me:
            continue
        try:
            # filesystem-read-ok: kernel process-group metadata; this isolated supervisor cannot import the Workspace seam
            stat_text = (entry / "stat").read_text(encoding="utf-8")
        except OSError:
            continue
        fields = stat_text.rsplit(")", 1)[1].split()
        # fields: state ppid pgrp ...; zombies are already dead.
        if int(fields[2]) == pgid and fields[0] not in ("Z", "X"):
            return True
    return False


def _kill_tree_and_exit() -> NoReturn:
    """Stop the entire PG this supervisor leads, then exit immediately.

    Called from the termination handler once the holder is gone. The PG includes the worker
    and every descendant the worker spawned, because the worker inherited
    our PG ID at ``fork`` time (``start_new_session=True`` was applied to
    the supervisor, not the worker, so the worker does not get a fresh PG).

    The group is sent SIGTERM first and given a bounded window to exit, so a
    git inside it can remove its lock files; only survivors are SIGKILLed.
    """
    pgid = os.getpid()
    # We are in the group too: ignore the SIGTERM we are about to broadcast.
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    # killpg against a dead group, a leader we no longer own, or a
    # permissions boundary all warrant the same outcome: we tried, we gave
    # up, exit so the holder's Popen doesn't linger.
    with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
        os.killpg(pgid, _SIGTERM)
        deadline = time.monotonic() + _GROUP_TERM_GRACE_S
        while time.monotonic() < deadline and _other_group_members_alive(pgid):
            # filesystem-poll-ok: bounded by the 5-second SIGTERM grace deadline; process-group exit has no event source
            time.sleep(0.05)
    with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
        os.killpg(pgid, _SIGKILL)
    os._exit(0)


def _forward_to_group(signum: int) -> None:
    """Re-send ``signum`` to the worker's PG without re-entering this handler."""
    handler = signal.getsignal(signum)
    signal.signal(signum, signal.SIG_IGN)
    try:
        with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
            os.killpg(os.getpid(), signum)
    finally:
        signal.signal(signum, handler)


def _termination_handler(holder_pid: int) -> Callable[[int, FrameType | None], None]:
    """Build the SIGTERM/SIGINT/SIGHUP handler for a supervisor owned by ``holder_pid``.

    A signal that arrives after the holder died (PR_SET_PDEATHSIG, or we were
    reparented) means nobody is left to escalate, so the whole PG is
    SIGKILLed. While the holder is alive the signal is a deliberate
    termination: it is forwarded to the worker's PG and the supervisor keeps
    waiting, so the worker gets the holder's grace period and the holder
    escalates to SIGKILL itself (the worker's own PDEATHSIG then follows the
    supervisor down).
    """

    def handle(signum: int, frame: FrameType | None) -> None:
        del frame
        if os.getppid() != holder_pid:
            _kill_tree_and_exit()
        _forward_to_group(signum)

    return handle


def run(argv: list[str]) -> int:
    """Supervisor entry point. ``argv[0]`` is the worker command path.

    Returns the worker's exit code (or 128+sig when the worker died from a
    signal). Exit codes from this supervisor itself:

    * ``126`` -- held back because ``set_pdeathsig`` couldn't arm and the
      caller wants a definitive "no containment" verdict.
    * ``127`` -- ``argv`` was empty or ``os.execvp`` failed.

    The supervisor has no other observable side effects on import; calling
    ``run`` does not require any module-level pre-condition.
    """
    sanitize_process_environment()
    if not argv:
        return 127

    # Install signal handlers BEFORE arming PR_SET_PDEATHSIG. If the holder
    # dies in the millisecond between prctl and sigaction, the SIGTERM would
    # land on the default handler and terminate only the supervisor; the
    # worker would then be orphaned. Keeping the order "sigaction first,
    # then prctl" closes that race.
    on_termination = _termination_handler(os.getppid())
    signal.signal(signal.SIGTERM, on_termination)
    signal.signal(signal.SIGINT, on_termination)
    signal.signal(signal.SIGHUP, on_termination)

    if not _set_pdeathsig(_SIGTERM):
        # Linux-only code path called the supervisor; if prctl cannot arm
        # we are no safer than the legacy spawn, but at least we fail loudly
        # rather than silently leaving the worker unprotected.
        return 126

    pid = os.fork()
    if pid == 0:
        # CHILD -- becomes the worker.
        # Reset inherited signal disposition back to SIG_DFL before exec so
        # the worker program does not see a stale handler pointing at our
        # termination handler (which references ``os._exit`` -- meaningless in the
        # exec'd program). ``os.execvp`` resets handlers too, but resetting
        # explicitly also closes the race if SIGTERM lands between fork
        # and exec.
        signal.signal(signal.SIGTERM, signal.SIG_DFL)
        signal.signal(signal.SIGINT, signal.SIG_DFL)
        signal.signal(signal.SIGHUP, signal.SIG_DFL)
        # Belt-and-suspenders: if the supervisor is SIGKILLed (uncatchable)
        # from the outside (e.g. ProcessManager.kill() upgraded to a
        # SIGKILL on the Popen), the worker's PR_SET_PDEATHSIG fires
        # against the supervisor as parent, so the kernel SIGKILLs the
        # worker too. Without this, SIGKILL on the supervisor leaves the
        # worker alive (orphaned to init).
        _set_pdeathsig(_SIGKILL)
        with contextlib.suppress(OSError):
            os.execvp(argv[0], argv)
        return 127

    # PARENT -- wait for the worker to exit; then return its status.
    while True:
        try:
            _, status = os.waitpid(pid, 0)
        except OSError:
            return 1
        if os.WIFEXITED(status):
            return os.WEXITSTATUS(status)
        if os.WIFSIGNALED(status):
            return 128 + os.WTERMSIG(status)


def main() -> int:
    """Supervisor CLI entry point; sanitizes environment before spawning worker."""
    sanitize_process_environment()
    return run(sys.argv[1:])


if __name__ == "__main__":
    sys.exit(main())
