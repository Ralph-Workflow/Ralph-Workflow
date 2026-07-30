"""Lock-liveness probe extracted from :mod:`ralph.pipeline.auto_integrate_recovery`.

When the crash-recovery path finds a stale ``index.lock`` (or
``HEAD.lock`` / ``refs/.../lock``) left behind by a previous run, it
must decide between two outcomes:

* **Live holder** (process ``pid`` is still running): this is
  *contention*, not *staleness* -- the lock MUST be left in place and
  the bounded retry loop backs off. Removing a live holder's lock
  silently corrupts the concurrent writer's checkout.
* **Dead holder** (the recorded PID is gone, or the lock carries no
  PID): this is *staleness* -- the lock is safe to clear.

The four helpers in this module implement the second column: read
the lock's PID text, parse it, and probe the OS for liveness. They
are extracted so :mod:`ralph.pipeline.auto_integrate_recovery` stays
under the ``_MAX_FILE_LINES`` cap while the reclaim path keeps
calling :func:`_lock_holder_is_dead` from the recovery namespace
via ``from ralph.pipeline.auto_integrate_recovery_lock import
_lock_holder_is_dead`` -- that re-export preserves the existing
``recover_incomplete_integration._lock_holder_is_dead`` seam used
by ``ralph.pipeline.auto_integrate_catalog_rationales`` and any
future call site.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


def _lock_holder_is_dead(lock_path: Path) -> bool:
    """True when the index.lock holder PID is provably dead (A9).

    A live holder is contention (E9), not staleness -- the lock
    MUST be left in place so the concurrent writer finishes, and
    the bounded retry loop backs off. A PID that is missing,
    unreadable, or that the OS reports as ``NoSuchProcess`` is
    treated as dead; any other error (a sandbox that hides
    ``/proc`` etc.) is treated as LIVE so a missed reclaim costs
    one backoff rather than a corrupt checkout.

    The PID is read via the standard git convention: a single
    line of plain text (the writing process's PID) in the lock
    file itself. Older gits wrote nothing here, so an empty /
    whitespace-only file is treated as "no PID", which the
    spec resolves as dead (A9: "liveness check, not age").
    """
    raw = _read_lock_pid_text(lock_path)
    if raw is None:
        return False
    if raw == "":
        return True
    pid = _parse_lock_pid(raw)
    if pid is None or pid <= 0:
        return True
    return _pid_is_alive(pid) is False


def _read_lock_pid_text(lock_path: Path) -> str | None:
    """Read the lock file's PID text, returning None on read error.

    ``None`` distinguishes an I/O failure from an empty file (which
    the spec treats as "no PID recorded"). The caller maps I/O failure
    to ``live`` so a missed reclaim costs one backoff, not a corrupt
    checkout.
    """
    try:
        return lock_path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None


def _parse_lock_pid(raw: str) -> int | None:
    """Parse the first line of the lock file as an integer PID.

    Returns ``None`` when the line is not a valid positive integer;
    the caller treats that as "no PID recorded" (dead).
    """
    try:
        return int(raw.splitlines()[0])
    except ValueError:
        return None


def _pid_is_alive(pid: int) -> bool:
    """True when the OS reports the PID is alive (signal 0 succeeded)."""
    import os

    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except (PermissionError, OSError):
        return True
    return True
