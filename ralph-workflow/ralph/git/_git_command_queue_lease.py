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

_AGENT_LEASE_FILENAME: str = "agent-lease.lock"


@dataclass(frozen=True)
class _WorktreeLeaseHolder:
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
    pass


class _WorktreeLease:
    def __init__(self, lock_path: Path, holder: _WorktreeLeaseHolder) -> None:
        self._lock_path = lock_path
        self._holder = holder
        self._fd: int | None = None
        self._released: bool = False

    @property
    def lock_path(self) -> Path:
        return self._lock_path

    @property
    def holder(self) -> _WorktreeLeaseHolder:
        return self._holder

    def release(self) -> None:
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
    return git_dir / "ralph" / _AGENT_LEASE_FILENAME


def _acquire_lock_file(
    lock_path: Path,
    holder: _WorktreeLeaseHolder,
    *,
    clock: LeaseClock,
) -> int:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    payload = lease_payload(holder.name, clock=clock)
    fd = os.open(
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
        _discard_created_lock_file(fd, lock_path)
        raise
    if written != len(payload):
        _discard_created_lock_file(fd, lock_path)
        msg = f"short write to lease file {lock_path!r}"
        raise OSError(errno.EIO, msg, str(lock_path))
    return fd


def _discard_created_lock_file(fd: int, lock_path: Path) -> None:
    if lease_fd_owns_path(fd, lock_path):
        with contextlib.suppress(OSError):
            lock_path.unlink()
    with contextlib.suppress(OSError):
        os.close(fd)


def _reclaim_dead_lease(
    lock_path: Path,
    holder: _WorktreeLeaseHolder,
    *,
    stale_seconds: float,
    probe: Callable[[Path, int | None], LeaseLiveness],
    clock: LeaseClock,
) -> tuple[LeaseStealDecision, int | None]:
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
    record = _read_lease_record(lock_path, clock=clock)
    if record is None:
        return False
    return probe(lock_path, record.holder_pid) != "dead"
