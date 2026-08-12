"""Single-owner advisory flock for cross-process workspace watch coordination.

Two independent processes must not each register their own recursive
watchdog observer on the same workspace root: that duplicates fseventsd
streams on macOS and multiplies inotify watches on Linux. This module
provides a best-effort, advisory, single-owner lock so the first process
to start a workspace monitor becomes the cross-process watch owner and
every other process falls back visibly to ``live_fallback`` instead of
registering an overlapping observer.

The lock is held for the full lifetime of the owning monitor's lease
(until ``release`` or process exit). ``fcntl.flock`` (POSIX) and
``msvcrt.locking`` (Windows) both release automatically when the owning
process dies, so a crashed owner cannot strand the lock.

This is a cooperative advisory lock: a non-Ralph process that watches
the workspace is unaffected. The lock file lives at
``<workspace_root>/.agent/.watchdog.lock`` alongside the other
``.agent`` bookkeeping sidecars, mirroring the ``_metadata_lock``
pattern from ``ralph.workspace.agent_dir_retention``.
"""

from __future__ import annotations

import os
import sys
import threading
from contextlib import suppress
from typing import TYPE_CHECKING, ClassVar

if sys.platform == "win32":
    import msvcrt
else:
    import fcntl

if TYPE_CHECKING:
    from pathlib import Path
    from typing import TextIO


_WATCHDOG_LOCK_RELPATH = ".agent/.watchdog.lock"


class CrossProcessWatchLock:
    """Single-owner advisory flock with full-lifetime semantics.

    ``try_acquire`` is the single entry point for a prospective watch
    owner. It atomically claims the lock when the workspace is free and
    returns ``None``; when another process already holds the lock it
    returns that holder's owner id so the caller can report it. ``release``
    drops the lock only when the caller's owner id matches the recorded
    holder, so a stale release from a previous owner cannot drop the
    active holder's lock.

    The lock is held by keeping the lock file descriptor open for the
    process's lifetime (or until ``release``). A process-local registry
    keyed by canonical workspace root tracks the open handle and the
    owner id so ``release`` can match without re-reading the file.
    """

    _holds: ClassVar[dict[str, tuple[TextIO, str]]] = {}  # bounded-accumulator-ok: release removes the final workspace entry
    _counter_lock: ClassVar[threading.Lock] = threading.Lock()
    _counter: ClassVar[int] = 0

    @classmethod
    def try_acquire(cls, workspace_root: Path) -> str | None:
        """Try to claim the cross-process watch lock for ``workspace_root``.

        Returns ``None`` when the lock was free and this call atomically
        claimed it (or this process already holds it): the caller may
        proceed and become the cross-process watch owner. Returns the
        current holder's owner id (a non-empty string) when the lock is
        held by another process: the caller must fall back and not
        register an overlapping observer.

        On the free path the claimed owner id is retrievable via
        ``claimed_owner_id`` so the winning lease can store it for its
        matching ``release`` call.

        Filesystem errors are best-effort: when the lock sidecar cannot
        be opened the call returns ``None`` (proceed standalone) rather
        than blocking observation, since the cross-process coordination
        is a best-effort optimization, not a correctness requirement.
        """
        key = str(workspace_root.absolute())
        with cls._counter_lock:
            if key in cls._holds:
                # This process already owns the lock; the caller may proceed.
                return None

        lock_path = workspace_root / _WATCHDOG_LOCK_RELPATH
        try:
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            # filesystem-write-ok: persistent advisory-lock sidecar serializes cross-process watchdog ownership.
            handle = lock_path.open("a+", encoding="utf-8")
        except OSError:
            # Best-effort: proceed without cross-process coordination.
            return None

        if not cls._try_lock(handle):
            with suppress(OSError):
                handle.close()
            return cls._read_holder(lock_path)

        owner_id = cls._mint_owner_id()
        try:
            handle.seek(0)
            handle.truncate()
            handle.write(owner_id)
            handle.flush()
        except OSError:
            cls._unlock(handle)
            with suppress(OSError):
                handle.close()
            return None
        with cls._counter_lock:
            cls._holds[key] = (handle, owner_id)
        return None

    @classmethod
    def release(cls, workspace_root: Path, owner_id: str) -> None:
        """Release the lock when ``owner_id`` matches the recorded holder.

        A stale release (wrong owner id, or this process does not hold
        the lock) is a silent no-op so a monitor whose lease was already
        released cannot drop the active holder's lock. ``owner_id`` must
        match the value returned by ``claimed_owner_id`` at acquire time.
        """
        key = str(workspace_root.absolute())
        with cls._counter_lock:
            entry = cls._holds.get(key)
            if entry is None:
                return
            handle, held_owner_id = entry
            if held_owner_id != owner_id:
                return
            cls._holds.pop(key, None)
        cls._unlock(handle)
        with suppress(OSError):
            handle.close()

    @classmethod
    def claimed_owner_id(cls, workspace_root: Path) -> str | None:
        """Return this process's owner id for ``workspace_root``, if held.

        The winning lease stores this so its ``stop()`` can call
        ``release`` with the matching id. Returns ``None`` when this
        process does not hold the lock for the workspace.
        """
        key = str(workspace_root.absolute())
        with cls._counter_lock:
            entry = cls._holds.get(key)
        if entry is None:
            return None
        return entry[1]

    @classmethod
    def _try_lock(cls, handle: TextIO) -> bool:
        """Attempt a non-blocking exclusive lock; return ``False`` on contention."""
        try:
            if sys.platform == "win32":
                # msvcrt.locking operates from the current file position;
                # ensure at least one byte exists and seek to the start.
                if handle.tell() == 0:
                    handle.write("0")
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return False
        return True

    @classmethod
    def _unlock(cls, handle: TextIO) -> None:
        """Release the advisory lock (best-effort, never raises)."""
        if sys.platform == "win32":
            with suppress(OSError):
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            with suppress(OSError):
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    @classmethod
    def _mint_owner_id(cls) -> str:
        """Return a process-unique owner id (pid + monotonic counter)."""
        with cls._counter_lock:
            cls._counter += 1
            counter = cls._counter
        return f"{os.getpid()}:{counter}"

    @classmethod
    def _read_holder(cls, lock_path: Path) -> str:
        """Best-effort read of the recorded holder owner id."""
        try:
            text = lock_path.read_text(encoding="utf-8").strip()
        except OSError:
            return "unknown"
        return text or "unknown"


__all__ = ["CrossProcessWatchLock"]
