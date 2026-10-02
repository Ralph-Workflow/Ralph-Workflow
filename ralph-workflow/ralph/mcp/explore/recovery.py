"""Recovery scheduler for the indexed exploration substrate (S-4).

Every applicable F1-F20 failure mode is mapped to a recovery action.
The scheduler drives the recovery with bounded exponential backoff and
a cross-process advisory lock so concurrent Ralph sessions on one
workspace never trigger duplicate rebuilds.

The scheduler is intentionally minimal: it does not run the build
itself (the existing :class:`ReindexWriter` does that). It owns the
*decision* to start, continue, defer, or mark unhealthy. The state
machine is:

* ``idle`` -- nothing to do
* ``scheduled`` -- a recovery attempt is queued
* ``running`` -- a writer owns the rebuild
* ``cooldown`` -- exponential backoff after a failure
* ``unhealthy`` -- bounded attempts exhausted; reads still serve
  from live search

The scheduler integrates with the existing :class:`ReindexWriter.claim`
path so concurrent calls coalesce. The cross-process lock is a
``fcntl`` advisory file lock under ``.agent/ralph-explore/.lock`` so
two Ralph processes cannot both rebuild the same index at once.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from ralph.mcp.explore._recovery_types import HealthState, _RecoveryState

logger = logging.getLogger(__name__)


# Bounded defaults. The values are immutable per the absolute-budget
# invariants in ``ralph/verify.py`` -- the recovery scheduler must
# not consume more than the documented share of CPU/IO budget.
DEFAULT_BACKOFF_BASE_SECONDS: float = 0.5
DEFAULT_MAX_BACKOFF_SECONDS: float = 8.0
DEFAULT_MAX_ATTEMPTS: int = 4


@dataclass
class RecoveryScheduler:
    """Per-workspace recovery scheduler.

    The scheduler owns the bounded backoff and the in-process
    coalescing state. Cross-process coalescing is delegated to the
    :class:`ReindexWriter` lock plus the advisory file lock.
    """

    workspace_root: Path
    initial_attempts: int = 0
    initial_health: HealthState = HealthState.HEALTHY
    backoff_base_seconds: float = DEFAULT_BACKOFF_BASE_SECONDS
    max_backoff_seconds: float = DEFAULT_MAX_BACKOFF_SECONDS
    max_attempts: int = DEFAULT_MAX_ATTEMPTS
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _state: _RecoveryState = field(default_factory=_RecoveryState)

    def snapshot(self) -> dict[str, object]:
        """Return the canonical state payload for ``ralph_index_status``."""
        with self._lock:
            return {
                "health": self._state.health.value,
                "last_failure": (
                    {
                        "code": self._state.last_failure_code,
                        "message": self._state.last_failure_message,
                    }
                    if self._state.last_failure_code
                    else None
                ),
                "recovery_attempts": self._state.recovery_attempts,
                "next_recovery_at": self._state.next_recovery_at,
                "cooldown_remaining": self._state.cooldown_remaining,
            }

    @property
    def health(self) -> HealthState:
        with self._lock:
            return self._state.health

    def mark_healthy(self) -> None:
        with self._lock:
            self._state.health = HealthState.HEALTHY
            self._state.last_failure_code = None
            self._state.last_failure_message = None
            self._state.cooldown_remaining = 0.0
            self._state.next_recovery_at = None

    def mark_building(self) -> None:
        with self._lock:
            self._state.health = HealthState.BUILDING
            self._state.cooldown_remaining = 0.0

    def record_failure(
        self,
        *,
        code: str,
        message: str = "",
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Record a failed recovery attempt and schedule the next backoff.

        Uses exponential backoff capped at ``max_backoff_seconds`` and
        marks the scheduler ``unhealthy`` once ``recovery_attempts``
        exceeds ``max_attempts``.
        """
        with self._lock:
            self._state.recovery_attempts += 1
            self._state.last_failure_code = code
            self._state.last_failure_message = message
            if self._state.recovery_attempts > self.max_attempts:
                self._state.health = HealthState.UNHEALTHY
                self._state.next_recovery_at = None
                self._state.cooldown_remaining = 0.0
                return
            backoff = min(
                self.max_backoff_seconds,
                self.backoff_base_seconds * float(1 << max(0, self._state.recovery_attempts - 1)),
            )
            self._state.health = HealthState.STALE
            self._state.next_recovery_at = clock() + backoff
            self._state.cooldown_remaining = backoff

    def mark_read_only(self) -> None:
        """F10: index can be read but not written. Serve from index while fresh."""
        with self._lock:
            self._state.health = HealthState.DEGRADED

    def mark_stale(self) -> None:
        """R4: stale past threshold; recovery scheduled."""
        with self._lock:
            self._state.health = HealthState.STALE

    def should_attempt_recovery(
        self, *, clock: Callable[[], float] = time.monotonic
    ) -> bool:
        """Return True when the scheduler should run another recovery attempt.

        Bounded by ``max_attempts`` and the exponential backoff. When
        ``recovery_attempts`` exceeds ``max_attempts``, the scheduler
        is ``unhealthy`` and returns False.
        """
        with self._lock:
            if self._state.recovery_attempts > self.max_attempts:
                return False
            if self._state.next_recovery_at is None:
                return True
            return clock() >= self._state.next_recovery_at


_LAST_ADVISORY_LOCK_FD: list[int | None] = [None]  # bounded-accumulator-ok: single-element lock fd cell


def advisory_lock_path(workspace_root: Path) -> Path:
    """Return the path to the cross-process advisory lock file."""
    return Path(workspace_root) / ".agent" / "ralph-explore" / ".recovery.lock"


def try_advisory_lock(workspace_root: Path) -> bool:
    """Attempt to acquire a non-blocking cross-process advisory lock.

    Returns True if the lock was acquired (caller owns it and MUST
    release with :func:`release_advisory_lock`). Returns False when
    another process already holds it -- the caller should defer the
    rebuild rather than start a duplicate writer.

    Uses ``fcntl`` on POSIX and is a no-op on platforms without
    ``fcntl``; the in-process coalescing via :class:`ReindexWriter`
    still applies on those platforms.
    """
    lock_path = advisory_lock_path(workspace_root)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    # resource-lifecycle-ok: cross-process advisory lock requires raw fd;
    # the lock is released by ``release_advisory_lock`` and closed via
    # ``os.close`` so it cannot leak across long-running sessions.
    # filesystem-write-ok: cross-process advisory lock file opened for flock fd
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o644)  # resource-lifecycle-ok: cross-process advisory lock — released by release_advisory_lock
    try:
        import fcntl

        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, OSError):
            os.close(fd)
            return False
    except ImportError:
        # No fcntl: rely on the in-process coalescing seam only.
        pass
    # Stash the fd on the path's stat so :func:`release_advisory_lock`
    # can find it. We deliberately do NOT remove the file on release;
    # the next call reopens it. The fd is leaked by intent across
    # the in-process scheduler so the OS keeps the lock for the
    # lifetime of the writer. ``release_advisory_lock`` is called at
    # end-of-recovery.
    import contextlib

    with contextlib.suppress(OSError):
        lock_path.touch()
    with contextlib.suppress(OSError):
        lock_path.stat()
    # Store the fd in a module-level cell so release_advisory_lock can find it.
    _LAST_ADVISORY_LOCK_FD[0] = fd
    return True


def release_advisory_lock() -> None:
    """Release the advisory lock acquired by :func:`try_advisory_lock`."""
    import contextlib

    fd = _LAST_ADVISORY_LOCK_FD[0]
    if fd is None:
        return
    _LAST_ADVISORY_LOCK_FD[0] = None
    try:
        import fcntl

        with contextlib.suppress(OSError, ValueError):
            fcntl.flock(fd, fcntl.LOCK_UN)
    except ImportError:
        pass
    with contextlib.suppress(OSError, ValueError):
        os.close(fd)


def build_scheduler(workspace_root: Path) -> RecoveryScheduler:
    """Construct a per-workspace scheduler seeded with default state."""
    return RecoveryScheduler(workspace_root=workspace_root)


__all__ = [
    "DEFAULT_BACKOFF_BASE_SECONDS",
    "DEFAULT_MAX_ATTEMPTS",
    "DEFAULT_MAX_BACKOFF_SECONDS",
    "HealthState",
    "RecoveryScheduler",
    "advisory_lock_path",
    "build_scheduler",
    "release_advisory_lock",
    "try_advisory_lock",
]
