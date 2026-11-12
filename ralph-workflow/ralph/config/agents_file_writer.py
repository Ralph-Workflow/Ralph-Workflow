from __future__ import annotations

import contextlib
import hashlib
import logging
import os
import stat
import sys
import tempfile
import threading
import tomllib
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

if sys.platform != "win32":
    import fcntl

if TYPE_CHECKING:
    from collections.abc import Iterator

_std_logger = logging.getLogger(__name__)

_NEW_FILE_MODE = 0o644

__all__ = ["AgentsFileConflictError", "write_agents_toml"]


class AgentsFileConflictError(OSError):
    pass


_PATH_LOCKS: dict[Path, threading.Lock] = {}
_PATH_LOCKS_GUARD = threading.Lock()


def _get_target_lock(target: Path) -> threading.Lock:
    with _PATH_LOCKS_GUARD:
        lock = _PATH_LOCKS.get(target)
        if lock is None:
            lock = threading.Lock()
            _PATH_LOCKS[target] = lock
        return lock


def _get_uid() -> int:
    try:
        return os.getuid()
    except AttributeError:
        return 0


def _lock_file_path(target: Path) -> Path:
    uid = _get_uid()
    target_key = hashlib.sha256(str(target.resolve()).encode("utf-8")).hexdigest()[:16]
    return Path(tempfile.gettempdir()) / f".ralph-agents-{uid}-{target_key}.lock"


@contextmanager
def _target_serialization_lock(target: Path) -> Iterator[None]:
    target_lock = _get_target_lock(target)
    acquired = target_lock.acquire(timeout=0.1)
    lock_fd: int | None = None
    try:
        if acquired:
            try:
                lock_path = _lock_file_path(target)
                lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
                if sys.platform != "win32":
                    fcntl.flock(lock_fd, fcntl.LOCK_EX)
            except OSError:
                if lock_fd is not None:
                    with contextlib.suppress(OSError):
                        os.close(lock_fd)
                    lock_fd = None
        yield
    finally:
        if lock_fd is not None:
            if sys.platform != "win32":
                with contextlib.suppress(OSError):
                    fcntl.flock(lock_fd, fcntl.LOCK_UN)
            with contextlib.suppress(OSError):
                os.close(lock_fd)
        if acquired:
            target_lock.release()


def _raise_conflict(path: Path, trigger: str) -> None:
    msg = (
        f"Conflict detected while writing {path} (trigger: {trigger}): "
        f"the file was modified concurrently. "
        f"Aborting write to preserve operator edits without overwrite. "
        f"Inspect {path} or re-run with explicit recovery (e.g. ralph --regenerate-config)."
    )
    logger.warning(msg)
    _std_logger.warning(msg)
    raise AgentsFileConflictError(msg)


def _read_existing_bytes(target: Path) -> bytes | None:
    try:
        return target.read_bytes()
    except FileNotFoundError:
        return None


def _resolve_mode(target: Path) -> int:
    try:
        return stat.S_IMODE(target.stat().st_mode)
    except FileNotFoundError:
        return _NEW_FILE_MODE


def _publish_backup(bak_path: Path, previous_bytes: bytes, directory: Path) -> None:
    staging = directory / f".{bak_path.name}.stage.{os.urandom(8).hex()}"
    descriptor = os.open(staging, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        staged = os.fdopen(descriptor, "wb")
    except BaseException:
        os.close(descriptor)
        raise
    try:
        with staged:
            staged.write(previous_bytes)
            staged.flush()
        staging.replace(bak_path)
    except BaseException:
        with contextlib.suppress(OSError):
            staging.unlink(missing_ok=True)
        raise


def write_agents_toml(path: Path, text: str, *, trigger: str) -> bool:
    path = Path(path)

    try:
        tomllib.loads(text)
    except ValueError as err:
        raise ValueError(f"Invalid TOML content for {path}: {err}") from err

    new_bytes = text.encode("utf-8")
    write_target = path.resolve() if path.is_symlink() else path
    bak_path = path.with_name(f"{path.name}.bak")

    tmp_path: Path | None = None
    try:
        with _target_serialization_lock(write_target):
            existing_bytes = _read_existing_bytes(write_target)
            if existing_bytes == new_bytes:
                return False

            file_mode = _resolve_mode(write_target)

            write_target.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp_path_str = tempfile.mkstemp(
                dir=write_target.parent,
                prefix=f".{write_target.name}.tmp.",
            )
            tmp_path = Path(tmp_path_str)
            with os.fdopen(fd, "wb") as handle:
                handle.write(new_bytes)
                handle.flush()
            tmp_path.chmod(file_mode)

            current_bytes = _read_existing_bytes(write_target)
            if current_bytes != existing_bytes:
                _raise_conflict(path, trigger)

            if existing_bytes is not None:
                _publish_backup(bak_path, existing_bytes, write_target.parent)

            final_bytes = _read_existing_bytes(write_target)
            if final_bytes != existing_bytes:
                _raise_conflict(path, trigger)

            tmp_path.replace(write_target)
    except BaseException:
        if tmp_path is not None:
            with contextlib.suppress(OSError):
                tmp_path.unlink(missing_ok=True)
        raise

    logger.info("Wrote {} (trigger: {}, backup: {})", path, trigger, bak_path)
    _std_logger.info("Wrote %s (trigger: %s, backup: %s)", path, trigger, bak_path)
    return True
