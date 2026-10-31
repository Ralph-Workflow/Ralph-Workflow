"""Cross-worktree lock and throttle for remote catch-up synchronization.

The catch-up ticker of every worktree of one repository shares one fetch
cadence per (remote, target): :func:`remote_sync_transaction` hands out the
lock and decides whether this tick may fetch. The cadence is
``general.auto_integrate_remote_interval_seconds`` when it is a positive,
finite number (:func:`remote_sync_interval_seconds`) and
:data:`REMOTE_SYNC_INTERVAL_SECONDS` (300 s) otherwise. ``0.0``, the field's
default, means "fetch at every seam" for the commit, phase and startup
seams; the background ticker keeps the 300 s fleet cadence for it.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import sys
import time
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final, cast

from git import Repo
from git.exc import InvalidGitRepositoryError, NoSuchPathError

if sys.platform == "win32":
    import msvcrt
else:
    import fcntl

if TYPE_CHECKING:
    from collections.abc import Iterator
    from typing import TextIO

    from ralph.config.models import UnifiedConfig


_COORDINATION_DIRECTORY: Final = "ralph/auto-integrate-catchup"
REMOTE_SYNC_INTERVAL_SECONDS: Final = 300.0


def remote_sync_interval_seconds(config: UnifiedConfig | None) -> float:
    """Return the ticker's fleet-wide fetch cadence for ``config``.

    A positive, finite ``general.auto_integrate_remote_interval_seconds``
    is honoured as configured. ``0.0`` (the default: the seams fetch every
    time) and any unusable value fall back to
    :data:`REMOTE_SYNC_INTERVAL_SECONDS`, so the background ticker never
    fetches on every 30-second tick of every worktree.
    """
    general: object = getattr(config, "general", None)
    configured: object = getattr(general, "auto_integrate_remote_interval_seconds", None)
    if isinstance(configured, bool) or not isinstance(configured, (int, float)):
        return REMOTE_SYNC_INTERVAL_SECONDS
    interval = float(configured)
    if not math.isfinite(interval) or interval <= 0.0:
        return REMOTE_SYNC_INTERVAL_SECONDS
    return interval


@dataclass(slots=True)
class RemoteSyncLease:
    """Owned remote-sync transaction with one shared fetch decision."""

    fetch_allowed: bool
    _state_path: Path
    _interval_seconds: float

    def record_fetch(self) -> None:
        _write_fetch_after(self._state_path, time.time() + self._interval_seconds)


def _common_git_dir(root: Path) -> Path | None:
    repo: Repo | None = None
    try:
        repo = Repo(root)
        return Path(repo.common_dir).resolve()
    except (InvalidGitRepositoryError, NoSuchPathError, OSError, ValueError):
        return None
    finally:
        if repo is not None:
            with suppress(Exception):
                repo.close()


def _try_lock(handle: TextIO) -> bool:
    try:
        if sys.platform == "win32":
            handle.write("0")
            handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    return True


def _unlock(handle: TextIO) -> None:
    if sys.platform == "win32":
        with suppress(OSError):
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        with suppress(OSError):
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _write_fetch_after(state_path: Path, fetch_after: float) -> None:
    state: dict[str, float] = {"fetch_after": fetch_after}
    payload = json.dumps(state, separators=(",", ":"))
    temporary = state_path.with_suffix(f".{os.getpid()}.tmp")
    temporary.write_text(  # filesystem-write-ok: atomic staging file replaced below
        payload, encoding="utf-8"
    )
    temporary.replace(state_path)


def _fetch_allowed(state_path: Path, interval_seconds: float) -> bool:
    if interval_seconds <= 0.0:
        return True
    try:
        payload = cast(
            "dict[str, str | float | int]",
            json.loads(state_path.read_text(encoding="utf-8")),
        )  # cast-policy: seam: persisted JSON is parsed at this boundary
        fetch_after = float(payload["fetch_after"])
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return True
    if not math.isfinite(fetch_after):
        return True
    now = time.time()
    latest_defensible_deadline = now + interval_seconds
    if fetch_after > latest_defensible_deadline:
        # A rollback or corrupt future record must neither fetch every 30-second
        # tick nor suppress forever. Re-anchor and persist one full cadence while
        # the transaction lock is held, so every worktree observes one deadline.
        _write_fetch_after(state_path, latest_defensible_deadline)
        fetch_after = latest_defensible_deadline
    return now >= fetch_after


@contextmanager
def remote_sync_transaction(
    root: Path,
    remote: str,
    target: str,
    *,
    interval_seconds: float = REMOTE_SYNC_INTERVAL_SECONDS,
) -> Iterator[RemoteSyncLease | None]:
    """Acquire the cross-worktree remote lock and yield its throttle lease.

    ``interval_seconds`` is the fetch cadence (see
    :func:`remote_sync_interval_seconds`). A persisted deadline further away
    than one cadence is re-anchored to one cadence, so lowering the
    configured interval takes effect at once.
    """

    common_dir = _common_git_dir(root)
    if common_dir is None:
        yield None
        return
    key = hashlib.sha256(f"{common_dir}\0{remote}\0{target}".encode()).hexdigest()
    coordination_dir = common_dir / _COORDINATION_DIRECTORY
    lock_path = coordination_dir / f"{key}.lock"
    state_path = coordination_dir / f"{key}.json"
    try:
        coordination_dir.mkdir(parents=True, exist_ok=True)
        handle = lock_path.open(  # filesystem-write-ok: persistent advisory lock inode
            "a+", encoding="utf-8"
        )
    except OSError:
        yield None
        return
    try:
        if not _try_lock(handle):
            yield None
            return
        yield RemoteSyncLease(
            fetch_allowed=_fetch_allowed(state_path, interval_seconds),
            _state_path=state_path,
            _interval_seconds=interval_seconds,
        )
    finally:
        _unlock(handle)
        with suppress(OSError):
            handle.close()


__all__ = [
    "REMOTE_SYNC_INTERVAL_SECONDS",
    "RemoteSyncLease",
    "remote_sync_interval_seconds",
    "remote_sync_transaction",
]
