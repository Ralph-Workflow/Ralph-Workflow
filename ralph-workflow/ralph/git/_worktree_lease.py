"""Evidence-gated worktree-lease reclamation.

A worktree lease is the mutual-exclusion token between the agent phase and
the background mutators. Reclaiming it is the one operation where being
wrong is expensive in BOTH directions: stealing a LIVE lease lets two
writers mutate the same repository, while refusing to reclaim a DEAD
lease wedges the repository forever.

This module therefore refuses to reclaim on age alone. A steal requires
POSITIVE EVIDENCE that the recorded holder is dead, and that evidence comes
from the shared git lock liveness probe
(:func:`ralph.git._lock_recovery.lock_has_no_live_holder`) -- the same probe
that decides whether a stale ``index.lock`` may be unlinked. The probe
answers with one of three verdicts:

* ``"live"`` -- a live process still holds or uses the lease. The steal is
  REFUSED.
* ``"unknown"`` -- the probe could not prove anything (unreadable
  ``/proc``, a nested pid namespace, a probe that overran its budget). The
  steal is REFUSED: an indeterminate reading is never a licence to steal.
* ``"dead"`` -- no live holder remains. The steal is ALLOWED.

The decision itself is a PURE function (:func:`decide_lease_steal`) over the
age of the lease and the probe verdict, so it can be asserted directly in a
test without touching the filesystem. The age is a SECONDARY condition
only: it annotates the evidence and never authorises a steal on its own.

The takeover itself is atomic: the dead holder's lock-file is re-opened and
an exclusive ``flock`` is taken on it. The kernel drops that lock when the
holder dies, so taking it IS the positive evidence that nobody is holding
the lease any more, and only one contender can win it.

TIME IS INJECTED. The age of a lease is read from a clock, never from a
direct call into the real one: :func:`default_lease_clock` is the ONLY
place in this module that touches the real clock, and every other read
goes through the injected :class:`LeaseClock`. A test can therefore
drive the lease age deterministically -- no wall-clock wait, no sleeping,
and no dependence on how long the test machine took to get there.
"""

from __future__ import annotations

import contextlib
import errno
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from ralph.git._lock_liveness import _resolve_repo_identity
from ralph.git._lock_proc_inspect import _check_pid_liveness
from ralph.git._lock_recovery import lock_has_no_live_holder

if sys.platform != "win32":
    import fcntl

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = [
    "LeaseClock",
    "LeaseLiveness",
    "LeaseStealDecision",
    "LeaseStealReason",
    "decide_lease_steal",
    "default_lease_clock",
    "lease_age_seconds",
    "lease_fd_owns_path",
    "lease_payload",
    "probe_lease_holder_liveness",
    "reclaim_dead_lease",
    "take_lease_flock",
]


#: The liveness verdict of a lease holder, as produced by the shared git
#: lock liveness probe.
LeaseLiveness = Literal["live", "dead", "unknown"]

#: Why a lease steal was allowed or refused.
LeaseStealReason = Literal[
    "holder_dead",
    "holder_dead_and_stale",
    "holder_alive",
    "liveness_indeterminate",
]

#: Separator count in a ``<holder>|<timestamp>|<pid>`` lease line; a parsed
#: line must yield at least this many ``|``-split fields.
_LEASE_LINE_MIN_FIELDS: int = 2

#: Separator count in a lease line that also carries the holder PID.
_LEASE_LINE_FIELDS_WITH_PID: int = 3

#: Wall-clock budget for one holder-liveness probe. The probe only runs
#: when a lease file exists AND a caller contends for it, so it is kept
#: tight: a probe that cannot answer inside its budget reports
#: ``"unknown"`` and the steal is refused.
_PROBE_TIMEOUT_SECONDS: float = 1.0


@dataclass(frozen=True)
class LeaseStealDecision:
    """The verdict of the evidence gate that authorises reclaiming a lease.

    Attributes:
        allowed: Whether the lease may be reclaimed.
        reason: The stable reason code for the verdict.
        detail: A human-readable explanation naming the evidence.
    """

    allowed: bool
    reason: LeaseStealReason
    detail: str


@dataclass(frozen=True)
class _LeaseRecord:
    """What the lease lock-file says about its holder.

    Attributes:
        holder_name: The recorded holder name, or ``None`` when the
            lock-file is corrupt and the name cannot be recovered.
        holder_pid: The recorded holder PID, or ``None`` when the
            lock-file does not name one.
        age_seconds: How long ago the lease was taken. A corrupt lock-file
            falls back to the file's modification time.
    """

    holder_name: str | None
    holder_pid: int | None
    age_seconds: float


@dataclass(frozen=True)
class _LeaseClock:
    """The injectable time source of the lease age path.

    Two domains are needed because the lease records TWO kinds of time:

    * the MONOTONIC clock stamps the lease payload, so a lease cannot be
      aged across a wall-clock change;
    * the WALL clock reads the lock-file's modification time, which is
      the only age evidence left when the payload is corrupt.

    Attributes:
        monotonic: Returns the current monotonic time in seconds.
        wall_time: Returns the current wall-clock time in seconds.
    """

    monotonic: Callable[[], float]
    wall_time: Callable[[], float]


#: Public alias of the lease clock; see the module docstring.
LeaseClock = _LeaseClock


def default_lease_clock() -> LeaseClock:
    """Return the production lease clock (the real monotonic and wall clocks).

    This factory is the ONLY place in this module that reads the real
    clock. Every other read goes through the injected clock, so a test
    can drive the lease age without waiting for wall-clock time.
    """
    return LeaseClock(monotonic=time.monotonic, wall_time=time.time)


def _resolve_clock(clock: LeaseClock | None) -> LeaseClock:
    """Return the injected clock, or the production clock."""
    return clock if clock is not None else default_lease_clock()


def decide_lease_steal(
    *,
    age_seconds: float,
    liveness: LeaseLiveness,
    stale_seconds: float,
) -> LeaseStealDecision:
    """Decide whether a lease may be reclaimed. Pure: no I/O, no state.

    Args:
        age_seconds: How long ago the lease was taken.
        liveness: The verdict of the holder-liveness probe.
        stale_seconds: The secondary age threshold. It NEVER authorises a
            steal on its own -- it only annotates the positive evidence
            that the holder is dead.

    Returns:
        The :class:`LeaseStealDecision` for this evidence.
    """
    if liveness == "live":
        return LeaseStealDecision(
            False,
            "holder_alive",
            "a live process still holds the lease; refusing to steal it",
        )
    if liveness == "unknown":
        return LeaseStealDecision(
            False,
            "liveness_indeterminate",
            "the holder liveness probe could not prove the holder is dead; "
            "refusing to steal the lease",
        )
    if age_seconds > stale_seconds:
        return LeaseStealDecision(
            True,
            "holder_dead_and_stale",
            "the holder is dead and the lease is older than the stale threshold",
        )
    return LeaseStealDecision(
        True,
        "holder_dead",
        "the holder is dead; the lease is safe to reclaim",
    )


def probe_lease_holder_liveness(lock_path: Path, holder_pid: int | None) -> LeaseLiveness:
    """Probe whether the recorded lease holder is still alive.

    The probe is the SHARED git lock liveness probe, scoped to the holder
    the lease file names: the same per-process liveness check the stale
    ``index.lock`` recovery uses, applied to the recorded holder PID. It
    answers fast (one process, not the whole process table) and it fails
    closed -- a probe it cannot complete reports ``"unknown"``.

    A lock-file that names no holder (a legacy or corrupt payload) falls
    back to the repository-wide probe, which can only prove the negative.

    Args:
        lock_path: The lease lock-file (``<common-git-dir>/ralph/agent-lease.lock``).
        holder_pid: The holder PID recorded in the lock-file, if any.

    Returns:
        ``"live"``, ``"dead"`` or ``"unknown"``.
    """
    git_dir = lock_path.parent.parent
    if holder_pid is not None:
        verdict = _probe_holder_pid(lock_path, git_dir, holder_pid)
        if verdict != "unknown":
            return verdict
    return lock_has_no_live_holder(
        lock_path,
        git_dir,
        probe_timeout_s=_PROBE_TIMEOUT_SECONDS,
    )


def _probe_holder_pid(lock_path: Path, git_dir: Path, holder_pid: int) -> LeaseLiveness:
    """Run the shared per-process liveness check against the holder PID.

    A holder that still holds the lease file open is ``"live"``; a holder
    whose process is gone (or that provably cannot hold the lease) is
    ``"dead"``; anything the check cannot rule out is ``"unknown"``. On a
    platform with no procfs (macOS) a missing ``/proc/<pid>`` proves nothing
    about the holder, so the check answers ``"unknown"`` and the caller falls
    back to the repository-wide probe, which consults ``lsof`` there.
    """
    proc_root = Path("/proc")
    # filesystem-read-ok: platform probe for procfs itself, not a workspace read
    if not proc_root.is_dir():
        return "unknown"
    try:
        lock, live_cwds, common_worktrees_dir, gitdir_bytes, gitdir_common_bytes = (
            _resolve_repo_identity(lock_path, git_dir, None)
        )
        lock_resolved = str(lock.resolve())
        lock_stat = lock.stat()
    except OSError:
        return "unknown"
    return _check_pid_liveness(
        proc_root,
        str(holder_pid),
        lock_resolved,
        live_cwds,
        common_worktrees_dir,
        gitdir_bytes,
        gitdir_common_bytes,
        lock_stat.st_uid,
        (lock_stat.st_dev, lock_stat.st_ino),
        lock_stat.st_mtime,
    )


def lease_age_seconds(acquired_at: float, *, clock: LeaseClock | None = None) -> float:
    """Return how long ago the lease was taken, in seconds.

    Pure: no I/O, no state. The age is read from the injected clock, so
    a test can place the lease at any age it likes.

    Args:
        acquired_at: The monotonic timestamp the lease payload records.
        clock: The injected clock; the production clock when omitted.

    Returns:
        The age of the lease in seconds, never negative.
    """
    effective = _resolve_clock(clock)
    return max(0.0, effective.monotonic() - acquired_at)


def lease_payload(holder_name: str, *, clock: LeaseClock | None = None) -> bytes:
    """Return the lock-file payload written for a lease holder.

    The payload is ``<holder>|<monotonic timestamp>|<pid>``. The PID makes
    the holder identifiable after a crash; the timestamp is monotonic so a
    lease cannot be aged across a wall-clock change. The timestamp comes
    from the injected clock, so the writer and the reader of a payload
    must agree on the clock -- the reader computes the age from it.

    Args:
        holder_name: The name of the holder the payload records.
        clock: The injected clock; the production clock when omitted.
    """
    effective = _resolve_clock(clock)
    return f"{holder_name}|{effective.monotonic():.6f}|{os.getpid()}\n".encode()


def _read_lease_record(
    lock_path: Path,
    *,
    clock: LeaseClock | None = None,
) -> _LeaseRecord | None:
    """Return what the lease lock-file records, or ``None`` when there is none.

    A corrupt lock-file still yields a record (with an unknown holder and
    an age taken from the file's modification time): the file's EXISTENCE
    is what blocks a takeover, and the liveness probe -- not the payload --
    decides whether the holder is dead.
    """
    effective = _resolve_clock(clock)
    try:
        raw = lock_path.read_bytes()
        stat_result = lock_path.stat()
    except OSError:
        return None
    fallback_age = max(0.0, effective.wall_time() - stat_result.st_mtime)
    if not raw:
        return _LeaseRecord(holder_name=None, holder_pid=None, age_seconds=fallback_age)
    first_line = raw.split(b"\n", 1)[0].decode("utf-8", errors="replace")
    parts = first_line.split("|")
    if len(parts) < _LEASE_LINE_MIN_FIELDS or not parts[0]:
        return _LeaseRecord(holder_name=None, holder_pid=None, age_seconds=fallback_age)
    try:
        acquired_at = float(parts[1])
    except ValueError:
        return _LeaseRecord(holder_name=None, holder_pid=None, age_seconds=fallback_age)
    holder_pid: int | None = None
    if len(parts) >= _LEASE_LINE_FIELDS_WITH_PID:
        try:
            holder_pid = int(parts[2])
        except ValueError:
            holder_pid = None
    return _LeaseRecord(
        holder_name=parts[0],
        holder_pid=holder_pid,
        age_seconds=lease_age_seconds(acquired_at, clock=effective),
    )


def reclaim_dead_lease(
    lock_path: Path,
    holder_name: str,
    *,
    stale_seconds: float,
    probe: Callable[[Path, int | None], LeaseLiveness],
    clock: LeaseClock | None = None,
) -> tuple[LeaseStealDecision, int | None]:
    """Reclaim a lease whose holder is provably dead.

    Args:
        lock_path: The lease lock-file to reclaim.
        holder_name: The name of the new holder.
        stale_seconds: The secondary age threshold.
        probe: The holder-liveness probe to consult.
        clock: The injected clock; the production clock when omitted. The
            SAME clock must read the payload and write the new one, so
            the age of the lease stays in one time domain.

    Returns:
        The evidence verdict and the file descriptor of the new lease
        (``None`` when the steal was refused or lost the race).
    """
    effective = _resolve_clock(clock)
    record = _read_lease_record(lock_path, clock=effective)
    if record is None:
        return (
            LeaseStealDecision(
                False,
                "liveness_indeterminate",
                "the lease lock-file disappeared before it could be inspected",
            ),
            None,
        )
    decision = decide_lease_steal(
        age_seconds=record.age_seconds,
        liveness=probe(lock_path, record.holder_pid),
        stale_seconds=stale_seconds,
    )
    if not decision.allowed:
        return decision, None
    return decision, _takeover_lock_file(lock_path, holder_name, clock=effective)


def take_lease_flock(fd: int) -> bool:
    """Take an exclusive, non-blocking ``flock`` on the lease file.

    The kernel drops that lock when the holder dies, so taking it IS the
    positive evidence that nobody holds the lease any more -- and only one
    contender can win it.

    Returns:
        True when the lock was taken, False when a live holder still holds
        it. Platforms without ``fcntl`` fall back to the probe's verdict
        alone.
    """
    if sys.platform == "win32":
        return True
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    return True


def lease_fd_owns_path(fd: int, lock_path: Path) -> bool:
    """Whether ``lock_path`` still names the inode ``fd`` has open.

    A ``flock`` only excludes holders of the SAME inode. Once the file at
    the path has been unlinked or replaced, a lock on the old inode
    excludes nobody, so it is never evidence of holding the lease.

    Returns:
        True when the path's ``(st_dev, st_ino)`` equals the fd's; False
        when they differ or either cannot be read.
    """
    try:
        held = os.fstat(fd)
        current = lock_path.stat()  # filesystem-read-ok: lease inode identity probe
    except OSError:
        return False
    return (held.st_dev, held.st_ino) == (current.st_dev, current.st_ino)


def _takeover_lock_file(
    lock_path: Path,
    holder_name: str,
    *,
    clock: LeaseClock | None = None,
) -> int | None:
    """Replace a provably dead holder's lease file with ours.

    The lock-file is re-opened and an exclusive ``flock`` is taken on it.
    The kernel releases that lock when the previous holder dies, so the
    ``flock`` succeeding IS the positive evidence that nobody holds the
    lease any more -- and only one contender can win it.

    That holds only while the locked inode is still the one AT the path:
    between our ``open`` and our ``flock`` the file can be released
    (unlinked) and re-created by another acquirer, and a lock on the
    detached inode would let two holders coexist. The takeover is
    therefore refused unless the path still names the inode we locked.
    """
    try:
        fd = os.open(  # filesystem-write-ok: lease takeover; resource-lifecycle-ok: the lease release closes fd
            str(lock_path),
            os.O_RDWR | os.O_CLOEXEC,
        )
    except OSError:
        return None
    try:
        if not take_lease_flock(fd) or not lease_fd_owns_path(fd, lock_path):
            os.close(fd)
            return None
        payload = lease_payload(holder_name, clock=clock)
        os.ftruncate(fd, 0)
        os.lseek(fd, 0, os.SEEK_SET)
        written = os.write(fd, payload)
        if written != len(payload):
            msg = f"short write to lease file {lock_path!r}"
            raise OSError(errno.EIO, msg, str(lock_path))
        return fd
    except OSError:
        with contextlib.suppress(OSError):
            os.close(fd)
        return None
