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


LeaseLiveness = Literal["live", "dead", "unknown"]

LeaseStealReason = Literal[
    "holder_dead",
    "holder_dead_and_stale",
    "holder_alive",
    "liveness_indeterminate",
]

_LEASE_LINE_MIN_FIELDS: int = 2

_LEASE_LINE_FIELDS_WITH_PID: int = 3

_PROBE_TIMEOUT_SECONDS: float = 1.0


@dataclass(frozen=True)
class LeaseStealDecision:
    allowed: bool
    reason: LeaseStealReason
    detail: str


@dataclass(frozen=True)
class _LeaseRecord:
    holder_name: str | None
    holder_pid: int | None
    age_seconds: float


@dataclass(frozen=True)
class _LeaseClock:
    monotonic: Callable[[], float]
    wall_time: Callable[[], float]


LeaseClock = _LeaseClock


def default_lease_clock() -> LeaseClock:
    return LeaseClock(monotonic=time.monotonic, wall_time=time.time)


def _resolve_clock(clock: LeaseClock | None) -> LeaseClock:
    return clock if clock is not None else default_lease_clock()


def decide_lease_steal(
    *,
    age_seconds: float,
    liveness: LeaseLiveness,
    stale_seconds: float,
) -> LeaseStealDecision:
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
    if holder_pid > 0:
        try:
            os.kill(holder_pid, 0)
        except ProcessLookupError:
            return "dead"
        except OSError:
            pass
    proc_root = Path("/proc")
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
    effective = _resolve_clock(clock)
    return max(0.0, effective.monotonic() - acquired_at)


def lease_payload(holder_name: str, *, clock: LeaseClock | None = None) -> bytes:
    effective = _resolve_clock(clock)
    return f"{holder_name}|{effective.monotonic():.6f}|{os.getpid()}\n".encode()


def _read_lease_record(
    lock_path: Path,
    *,
    clock: LeaseClock | None = None,
) -> _LeaseRecord | None:
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
    if sys.platform == "win32":
        return True
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    return True


def lease_fd_owns_path(fd: int, lock_path: Path) -> bool:
    try:
        held = os.fstat(fd)
        current = lock_path.stat()
    except OSError:
        return False
    return (held.st_dev, held.st_ino) == (current.st_dev, current.st_ino)


def _takeover_lock_file(
    lock_path: Path,
    holder_name: str,
    *,
    clock: LeaseClock | None = None,
) -> int | None:
    try:
        fd = os.open(
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
