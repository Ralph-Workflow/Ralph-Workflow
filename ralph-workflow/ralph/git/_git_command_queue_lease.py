"""Worktree-lease mechanics behind the git command queue's lease handshake.

The worktree lease is the mutual-exclusion token between the agent phase and
the background mutators: whoever holds it owns the repository's on-disk state
for the duration of a phase. This module owns the lease FILE mechanics:

* creating the lock-file exclusively (``O_CREAT|O_EXCL``), so a second
  acquirer fails fast instead of racing the first one;
* stamping it with the holder identity and the INJECTED clock
  (``<holder>|<monotonic timestamp>|<pid>``);
* taking the kernel ``flock`` that IS the positive evidence that the previous
  holder is gone (the kernel drops it when the holder dies);
* reclaiming a lease whose holder the liveness probe proved dead.

The PUBLIC lease API (``begin_agent_lease``, ``try_acquire_lease``,
``end_agent_lease``, ``is_lease_held``) and the PUBLIC lease types live in
:mod:`ralph.git.git_command_queue`: the queue is the single entry point every
git code path routes through. The names here are PRIVATE so that the queue
module stays the one public import path for the handshake.

TIME IS INJECTED. Every age this module reads comes from the clock its
caller passes in (:class:`ralph.git._worktree_lease.LeaseClock`), so a test
can place a lease at any age without waiting for wall-clock time.
"""

from __future__ import annotations

import contextlib
import errno
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ralph.git._worktree_lease import (
    LeaseClock,
    LeaseStealDecision,
    _read_lease_record,
    lease_fd_owns_path,
    lease_payload,
    reclaim_dead_lease,
    take_lease_flock,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from ralph.git._worktree_lease import LeaseLiveness

#: Worktree lease lock-filename (placed under ``<common-git-dir>/ralph/``).
_AGENT_LEASE_FILENAME: str = "agent-lease.lock"


@dataclass(frozen=True)
class _WorktreeLeaseHolder:
    """The identity handed to the queue's ``begin_agent_lease``.

    A holder string is written into the lock-file so post-mortem
    inspections (and tests) can see who holds the lease. ``name`` MUST
    be short and printable (a single line, no slashes, no NULs).
    """

    name: str
    acquired_at_unix: float = 0.0

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("WorktreeLeaseHolder.name must not be empty")
        if any(ch in self.name for ch in ("\n", "\x00", "/", "\\")):
            raise ValueError(
                f"WorktreeLeaseHolder.name {self.name!r} contains an illegal character"
            )


class _WorktreeLeaseUnavailableError(RuntimeError):
    """Raised when an exclusive lease could not be acquired.

    Background mutators should never see this exception -- they use the
    queue's ``try_acquire_lease`` (which returns ``None`` on failure). It
    is only surfaced to ``begin_agent_lease`` callers when the agent
    phase must NOT proceed without an exclusive lease.
    """


class _WorktreeLease:
    """Context manager granted by the queue's ``begin_agent_lease``.

    The lease owns the lock-file at
    ``<common-git-dir>/ralph/agent-lease.lock``. ``__exit__`` removes
    the lock-file, closes the fd, and clears the cached handle so a
    follow-up ``begin_agent_lease`` call can succeed immediately.

    The lease is NOT thread-aware: ``__exit__`` from one thread while
    another holds the same Python descriptor is undefined. The current
    production use is always single-agent-phase per repo, so the
    concurrency model is "one phase, one thread".
    """

    def __init__(self, lock_path: Path, holder: _WorktreeLeaseHolder) -> None:
        self._lock_path = lock_path
        self._holder = holder
        self._fd: int | None = None
        self._released: bool = False

    @property
    def lock_path(self) -> Path:
        """Return the lock-file path this lease owns."""
        return self._lock_path

    @property
    def holder(self) -> _WorktreeLeaseHolder:
        """Return the identity of the holder that acquired this lease."""
        return self._holder

    def release(self) -> None:
        """Drop the lease and remove the lock-file.

        Idempotent: calling twice is safe. Safe to call from
        ``__exit__`` and from an explicit ``end_agent_lease``.

        The lock-file is unlinked WHILE the ``flock`` is still held, and
        only then is the fd closed. Closing first dropped the lock while
        the file was still at its path: a contender could take the
        ``flock`` on that inode in the gap, the unlink then detached it,
        and a third acquirer ``O_EXCL``-created a fresh file -- two
        holders at once. The unlink is also skipped when the path no
        longer names OUR inode, so a release never deletes another
        holder's lease. A crash-recovered lock-file (already removed by
        another process) does not error the caller.
        """
        if self._released:
            return
        self._released = True
        fd = self._fd
        self._fd = None
        if fd is None:
            return
        if lease_fd_owns_path(fd, self._lock_path):
            with contextlib.suppress(OSError):
                self._lock_path.unlink()
        with contextlib.suppress(OSError):
            os.close(fd)

    def __enter__(self) -> _WorktreeLease:
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.release()


def _lease_lock_path(git_dir: Path) -> Path:
    """Return the lease lock-file path for ``git_dir``."""
    return git_dir / "ralph" / _AGENT_LEASE_FILENAME


def _acquire_lock_file(
    lock_path: Path,
    holder: _WorktreeLeaseHolder,
    *,
    clock: LeaseClock,
) -> int:
    """Create ``lock_path`` with ``O_CREAT|O_EXCL``; return the open fd.

    On success the caller holds an open fd for the lifetime of the lease
    and an exclusive ``flock`` on it: the kernel drops that lock when the
    holder dies, so a contender can take it as positive evidence that the
    holder is gone. A missing parent directory is created; on creation
    failure the native OSError is propagated.

    The payload is stamped from the INJECTED clock, so the writer and the
    reader of a lease payload agree on the time domain.
    """
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    payload = lease_payload(holder.name, clock=clock)
    fd = os.open(  # filesystem-write-ok: atomic O_EXCL lease; resource-lifecycle-ok: lease release closes fd
        str(lock_path),
        os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_CLOEXEC,
        0o600,
    )
    try:
        if not take_lease_flock(fd):
            msg = f"could not lock the newly created lease file {lock_path!r}"
            raise OSError(errno.EAGAIN, msg, str(lock_path))
        written = os.write(fd, payload)
    except OSError:
        with contextlib.suppress(OSError):
            os.close(fd)
        with contextlib.suppress(OSError):
            lock_path.unlink()
        raise
    if written != len(payload):
        with contextlib.suppress(OSError):
            os.close(fd)
        with contextlib.suppress(OSError):
            lock_path.unlink()
        msg = f"short write to lease file {lock_path!r}"
        raise OSError(errno.EIO, msg, str(lock_path))
    return fd


def _reclaim_dead_lease(
    lock_path: Path,
    holder: _WorktreeLeaseHolder,
    *,
    stale_seconds: float,
    probe: Callable[[Path, int | None], LeaseLiveness],
    clock: LeaseClock,
) -> tuple[LeaseStealDecision, int | None]:
    """Reclaim a lease whose holder is provably dead.

    The age of the lease NEVER authorises the reclaim on its own: the
    shared git lock liveness probe must first produce positive evidence
    that the holder is dead. A live holder and an indeterminate probe both
    refuse the reclaim, and the refusal names the reason.

    Returns:
        The evidence verdict and the new fd (``None`` when refused).
    """
    return reclaim_dead_lease(
        lock_path,
        holder.name,
        stale_seconds=stale_seconds,
        probe=probe,
        clock=clock,
    )


def _acquire_lease(
    lock_path: Path,
    holder: _WorktreeLeaseHolder,
    *,
    stale_seconds: float,
    probe: Callable[[Path, int | None], LeaseLiveness],
    clock: LeaseClock,
) -> _WorktreeLease:
    """Acquire the lease file exclusively; raise when it cannot be reclaimed.

    A crashed prior holder is reclaimed ONLY when the liveness probe
    produces positive evidence that the holder is dead -- never because
    the lease is old.

    Raises:
        _WorktreeLeaseUnavailableError: Another live holder owns the
            lease, or the holder's liveness could not be proven dead. The
            message names the reason the reclaim was refused.
    """
    try:
        fd = _acquire_lock_file(lock_path, holder, clock=clock)
    except FileExistsError as exc:
        decision, stolen = _reclaim_dead_lease(
            lock_path,
            holder,
            stale_seconds=stale_seconds,
            probe=probe,
            clock=clock,
        )
        if stolen is None:
            raise _WorktreeLeaseUnavailableError(
                f"worktree lease for {lock_path} is not reclaimable: "
                f"{decision.reason} ({decision.detail})"
            ) from exc
        fd = stolen
    lease = _WorktreeLease(lock_path, holder)
    lease._fd = fd
    return lease


def _lease_is_held(
    lock_path: Path,
    *,
    probe: Callable[[Path, int | None], LeaseLiveness],
    clock: LeaseClock,
) -> bool:
    """Return True when a live lease exists for ``lock_path``.

    A lease counts as held unless the liveness probe has produced
    positive evidence that its holder is dead: a live holder and an
    INDETERMINATE probe both read as held, so a background mutator skips
    the tick rather than racing an agent it could not prove was gone.
    """
    record = _read_lease_record(lock_path, clock=clock)
    if record is None:
        return False
    return probe(lock_path, record.holder_pid) != "dead"
