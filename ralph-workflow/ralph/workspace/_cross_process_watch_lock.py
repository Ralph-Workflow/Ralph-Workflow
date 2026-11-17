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

from ralph.workspace._watch_lock_io_error import WatchLockIOError

if TYPE_CHECKING:
    from pathlib import Path
    from typing import TextIO


_WATCHDOG_LOCK_RELPATH = ".agent/.watchdog.lock"


class CrossProcessWatchLock:
    _holds: ClassVar[dict[str, tuple[TextIO, str]]] = {}
    _last_released: ClassVar[dict[str, str]] = {}
    _counter_lock: ClassVar[threading.Lock] = threading.Lock()
    _counter: ClassVar[int] = 0

    @classmethod
    def try_acquire(cls, workspace_root: Path) -> str | None:
        key = str(workspace_root.absolute())
        with cls._counter_lock:
            if key in cls._holds:
                return None

        lock_path = workspace_root / _WATCHDOG_LOCK_RELPATH
        try:
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            handle = lock_path.open("a+", encoding="utf-8")
        except OSError:
            return "io_error"

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
        except OSError as exc:
            cls._unlock(handle)
            with suppress(OSError):
                handle.close()
            raise WatchLockIOError(str(exc)) from exc
        with cls._counter_lock:
            cls._holds[key] = (handle, owner_id)
        return None

    @classmethod
    def release(cls, workspace_root: Path, owner_id: str) -> None:
        key = str(workspace_root.absolute())
        with cls._counter_lock:
            entry = cls._holds.get(key)
            if entry is None:
                return
            handle, held_owner_id = entry
            if held_owner_id != owner_id:
                return
            cls._holds.pop(key, None)
            cls._last_released[key] = owner_id
        cls._unlock(handle)
        with suppress(OSError):
            handle.close()

    @classmethod
    def claimed_owner_id(cls, workspace_root: Path) -> str | None:
        key = str(workspace_root.absolute())
        with cls._counter_lock:
            entry = cls._holds.get(key)
        if entry is None:
            return None
        return entry[1]

    @classmethod
    def last_released_holder(cls, workspace_root: Path) -> str | None:
        key = str(workspace_root.absolute())
        with cls._counter_lock:
            return cls._last_released.get(key)

    @classmethod
    def _try_lock(cls, handle: TextIO) -> bool:
        try:
            if sys.platform == "win32":
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
        if sys.platform == "win32":
            with suppress(OSError):
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            with suppress(OSError):
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    @classmethod
    def _mint_owner_id(cls) -> str:
        with cls._counter_lock:
            cls._counter += 1
            counter = cls._counter
        return f"{os.getpid()}:{counter}"

    @classmethod
    def _read_holder(cls, lock_path: Path) -> str:
        try:
            text = lock_path.read_text(encoding="utf-8").strip()
        except OSError:
            return "unknown"
        return text or "unknown"


__all__ = ["CrossProcessWatchLock", "WatchLockIOError"]
