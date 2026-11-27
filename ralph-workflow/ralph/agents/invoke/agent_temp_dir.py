from __future__ import annotations

import os
import shutil
import sys
import tempfile
import threading
import time
from collections import deque
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Final

import psutil
from loguru import logger

from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND
from ralph.mcp.artifacts.idempotent_write import write_text_if_changed

if TYPE_CHECKING:
    from collections.abc import Iterator

_DARWIN_SHORT_TEMP_ROOT: Final[Path] = Path("/private/tmp")
_POSIX_SHORT_TEMP_ROOT: Final[Path] = Path("/") / "tmp"
_MAX_ROOT_LENGTH: Final[int] = 32
_PREFIX: Final[str] = "ra"
_OWNER_STAMP: Final[str] = ".ralph-owner"
_NAME_LENGTH: Final[int] = len(_PREFIX) + 8
_UNSTAMPED_GRACE_SECONDS: Final[float] = 12 * 3600.0
_SWEEP_INTERVAL_SECONDS: Final[float] = 600.0
_last_sweep: deque[tuple[str, float]] = deque(maxlen=16)
_sweep_lock = threading.Lock()


def _default_root() -> Path:
    if sys.platform == "darwin" and DEFAULT_FILE_BACKEND.exists(_DARWIN_SHORT_TEMP_ROOT):
        return _DARWIN_SHORT_TEMP_ROOT
    system_root = Path(tempfile.gettempdir())
    if len(str(system_root)) > _MAX_ROOT_LENGTH and DEFAULT_FILE_BACKEND.exists(
        _POSIX_SHORT_TEMP_ROOT
    ):
        return _POSIX_SHORT_TEMP_ROOT
    return system_root


def _owner_stamp() -> str:
    pid = os.getpid()
    try:
        started = psutil.Process(pid).create_time()
    except (psutil.Error, OSError):
        started = 0.0
    return f"{pid} {started:.3f}"


def _owner_is_alive(stamp: str) -> bool:
    try:
        pid_text, started_text = stamp.split()
        pid, started = int(pid_text), float(started_text)
    except ValueError:
        return False
    try:
        return abs(psutil.Process(pid).create_time() - started) < 1.0
    except psutil.NoSuchProcess:
        return False
    except (psutil.Error, OSError):
        return True


def _is_abandoned(candidate: Path, now: float) -> bool:
    try:
        stamp = (candidate / _OWNER_STAMP).read_text(encoding="utf-8")
    except FileNotFoundError:
        try:
            return now - candidate.stat().st_mtime > _UNSTAMPED_GRACE_SECONDS
        except OSError:
            return False
    except OSError:
        return False
    return not _owner_is_alive(stamp.strip())


def sweep_abandoned_private_tmpdirs(root: Path | None = None, *, now: float | None = None) -> int:
    base = root or _default_root()
    moment = time.time() if now is None else now
    removed = 0
    try:
        entries = list(base.iterdir())
    except OSError:
        return 0
    for candidate in entries:
        if (
            len(candidate.name) != _NAME_LENGTH
            or not candidate.name.startswith(_PREFIX)
            or candidate.is_symlink()
            or not candidate.is_dir()
        ):
            continue
        if _is_abandoned(candidate, moment):
            remove_private_tmpdir(candidate)
            removed += 1
    if removed:
        logger.info("reclaimed {} abandoned private temp dir(s) under {}", removed, base)
    return removed


def _sweep_if_due(base: Path) -> None:
    key = str(base)
    now = time.monotonic()
    with _sweep_lock:
        last = next((at for root, at in reversed(_last_sweep) if root == key), None)
        if last is not None and now - last < _SWEEP_INTERVAL_SECONDS:
            return
        _last_sweep.append((key, now))
    threading.Thread(
        target=sweep_abandoned_private_tmpdirs,
        args=(base,),
        name="ralph-private-tmpdir-sweep",
        daemon=True,
    ).start()


def create_private_tmpdir(root: Path | None = None) -> Path:
    base = root or _default_root()
    _sweep_if_due(base)
    tmpdir = Path(tempfile.mkdtemp(prefix=_PREFIX, dir=base))
    write_text_if_changed(DEFAULT_FILE_BACKEND, tmpdir / _OWNER_STAMP, _owner_stamp())
    return tmpdir


def remove_private_tmpdir(tmpdir: Path) -> None:
    shutil.rmtree(tmpdir, ignore_errors=True)
    if DEFAULT_FILE_BACKEND.exists(tmpdir):
        logger.warning("agent temp dir {} could not be fully removed", tmpdir)


@contextmanager
def private_agent_tmpdir(root: Path | None = None) -> Iterator[Path]:
    tmpdir = create_private_tmpdir(root)
    try:
        yield tmpdir
    finally:
        remove_private_tmpdir(tmpdir)


__all__ = [
    "create_private_tmpdir",
    "private_agent_tmpdir",
    "remove_private_tmpdir",
    "sweep_abandoned_private_tmpdirs",
]
