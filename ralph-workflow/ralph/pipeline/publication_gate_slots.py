from __future__ import annotations

import fcntl
import os
import threading
import time
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import IO, TYPE_CHECKING, Final

from loguru import logger

from ralph.config._env import getenv

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Mapping

GATE_SLOTS_ENV: Final[str] = "RALPH_PUBLICATION_GATE_SLOTS"
GATE_SLOT_DIR_ENV: Final[str] = "RALPH_PUBLICATION_GATE_SLOT_DIR"
GATE_SLOT_WAIT_SECONDS: Final[float] = 1800.0
_CORES_PER_SLOT: Final[int] = 16
_RETRY_SECONDS: Final[float] = 5.0


def configured_gate_slots(env: Mapping[str, str] | None = None) -> int:
    raw = getenv(GATE_SLOTS_ENV, env)
    if raw is not None and raw.strip().isdigit() and int(raw.strip()) > 0:
        return int(raw.strip())
    return max(1, (os.cpu_count() or 1) // _CORES_PER_SLOT)


def gate_slot_directory(env: Mapping[str, str] | None = None) -> Path:
    configured = getenv(GATE_SLOT_DIR_ENV, env)
    if configured:
        return Path(configured)
    runtime = getenv("XDG_RUNTIME_DIR", env)
    if runtime:
        return Path(runtime) / "ralph-workflow" / "publication-gate-slots"
    state = getenv("XDG_STATE_HOME", env)
    base = Path(state) if state else Path.home() / ".local" / "state"
    return base / "ralph-workflow" / "publication-gate-slots"


def _try_slots(directory: Path, slots: int) -> IO[str] | None:
    directory.mkdir(parents=True, exist_ok=True)
    for index in range(slots):
        handle = (directory / f"slot-{index}.lock").open("a", encoding="utf-8")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            continue
        return handle
    return None


@contextmanager
def publication_gate_slot(
    *,
    slots: int | None = None,
    directory: Path | None = None,
    wait_seconds: float = GATE_SLOT_WAIT_SECONDS,
    monotonic: Callable[[], float] = time.monotonic,
    wait: Callable[[float], bool] | None = None,
) -> Iterator[bool]:
    slot_count = slots if slots is not None else configured_gate_slots()
    slot_dir = directory if directory is not None else gate_slot_directory()
    pause = wait if wait is not None else threading.Event().wait
    deadline = monotonic() + wait_seconds
    handle = _try_slots(slot_dir, slot_count)
    if handle is None:
        logger.info(
            "waiting for a publication gate slot: all {} host-wide slots in {} are busy "
            "(at most {:.0f}s)",
            slot_count,
            slot_dir,
            wait_seconds,
        )
    while handle is None and monotonic() < deadline:
        pause(min(_RETRY_SECONDS, max(0.0, deadline - monotonic())))
        handle = _try_slots(slot_dir, slot_count)
    try:
        yield handle is not None
    finally:
        if handle is not None:
            with suppress(OSError):
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            handle.close()


__all__ = [
    "GATE_SLOTS_ENV",
    "GATE_SLOT_DIR_ENV",
    "GATE_SLOT_WAIT_SECONDS",
    "configured_gate_slots",
    "gate_slot_directory",
    "publication_gate_slot",
]
