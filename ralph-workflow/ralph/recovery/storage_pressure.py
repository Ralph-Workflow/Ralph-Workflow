from __future__ import annotations

import errno
import shutil
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Final

from loguru import logger

from ralph.agents.system_clock import SystemClock
from ralph.recovery.volume_shortfall import GIB, VolumeShortfall, gib_text

if TYPE_CHECKING:
    from collections.abc import Callable, Hashable, Sequence

    from ralph.agents.clock import Clock

PAUSE_BELOW_FREE_BYTES: Final[int] = 2 * GIB

RESUME_AT_FREE_BYTES: Final[int] = 3 * GIB

STORAGE_REPOLL_INTERVAL_SECONDS: Final[float] = 15.0

STORAGE_HEARTBEAT_INTERVAL_SECONDS: Final[float] = 60.0

_OUT_OF_SPACE_ERRNOS: Final[frozenset[int]] = frozenset({errno.ENOSPC, errno.EDQUOT})

_OUT_OF_SPACE_MARKERS: Final[tuple[str, ...]] = (
    "[errno 28]",
    "no space left on device",
    "disk quota exceeded",
)

_MAX_CAUSE_DEPTH: Final[int] = 16

MAX_UNCONFIRMED_OUT_OF_SPACE_RETRIES: Final[int] = 3


def _disk_free(path: Path) -> int:
    return shutil.disk_usage(path).free


def _volume_of(path: Path) -> Hashable:
    return path.stat().st_dev


class StorageGuard:
    def __init__(
        self,
        paths: Sequence[Path],
        *,
        disk_free: Callable[[Path], int] = _disk_free,
        volume_of: Callable[[Path], Hashable] = _volume_of,
        wait: Callable[[float], None] | None = None,
        clock: Clock | None = None,
        pause_below_bytes: int = PAUSE_BELOW_FREE_BYTES,
        resume_at_bytes: int = RESUME_AT_FREE_BYTES,
        repoll_seconds: float = STORAGE_REPOLL_INTERVAL_SECONDS,
        heartbeat_seconds: float = STORAGE_HEARTBEAT_INTERVAL_SECONDS,
    ) -> None:
        self._paths = tuple(paths)
        self._disk_free = disk_free
        self._volume_of = volume_of
        self._clock: Clock = clock if clock is not None else SystemClock()
        self._wait = wait if wait is not None else self._clock.sleep
        self._pause_below_bytes = pause_below_bytes
        self._resume_at_bytes = max(resume_at_bytes, pause_below_bytes)
        self._repoll_seconds = repoll_seconds
        self._heartbeat_seconds = heartbeat_seconds

    def shortfalls(self, *, required_bytes: int | None = None) -> tuple[VolumeShortfall, ...]:
        needed = self._pause_below_bytes if required_bytes is None else required_bytes
        seen: set[Hashable] = set()
        found: list[VolumeShortfall] = []
        for path in self._paths:
            try:
                volume = self._volume_of(path)
                free = self._disk_free(path)
            except OSError:
                continue
            if volume in seen:
                continue
            seen.add(volume)
            if free < needed:
                found.append(VolumeShortfall(path=path, free_bytes=free, required_bytes=needed))
        return tuple(found)

    @property
    def resume_at_bytes(self) -> int:
        return self._resume_at_bytes

    def is_low(self) -> bool:
        return bool(self.shortfalls())

    def wait_until_clear(self, *, required_bytes: int | None = None) -> float:
        shortfalls = self.shortfalls(required_bytes=required_bytes)
        if not shortfalls:
            return 0.0
        logger.bind(recovery=True).warning(
            "Pipeline paused: low disk space. {}. Free disk space and Ralph Workflow resumes on its "
            "own once {} GiB is free on each volume.",
            "; ".join(shortfall.describe() for shortfall in shortfalls),
            gib_text(self._resume_at_bytes),
        )
        started = self._clock.monotonic()
        next_heartbeat = self._heartbeat_seconds
        while True:
            self._wait(self._repoll_seconds)
            waited = self._clock.monotonic() - started
            remaining = self.shortfalls(required_bytes=self._resume_at_bytes)
            if not remaining:
                logger.bind(recovery=True).info(
                    "Disk space recovered after {}s, resuming pipeline", int(waited)
                )
                return waited
            if waited >= next_heartbeat:
                next_heartbeat += self._heartbeat_seconds
                logger.bind(recovery=True).warning(
                    "Pipeline still paused for low disk space after {}s: {}",
                    int(waited),
                    "; ".join(shortfall.describe() for shortfall in remaining),
                )


def default_storage_guard(workspace_root: Path) -> StorageGuard:
    return StorageGuard((Path(tempfile.gettempdir()), Path.home(), workspace_root))


def is_out_of_space(exc: BaseException) -> bool:
    current: BaseException | None = exc
    for _ in range(_MAX_CAUSE_DEPTH):
        if current is None:
            return False
        if isinstance(current, OSError) and current.errno in _OUT_OF_SPACE_ERRNOS:
            return True
        text = str(current).lower()
        if any(marker in text for marker in _OUT_OF_SPACE_MARKERS):
            return True
        current = current.__cause__ or current.__context__
    return False


def run_step_with_storage_guard[R](
    step: Callable[[], R],
    *,
    guard: StorageGuard,
    is_failure: Callable[[R], bool],
    on_low_space_outcome: Callable[[], None] | None = None,
) -> R:
    unconfirmed_out_of_space = 0
    while True:
        guard.wait_until_clear()
        try:
            result = step()
        except Exception as exc:
            if not is_out_of_space(exc):
                raise
            logger.bind(recovery=True).warning(
                "Pipeline step ran out of disk space ({}); retrying it once space is free",
                exc,
            )
            if on_low_space_outcome is not None:
                on_low_space_outcome()
            if guard.wait_until_clear(required_bytes=guard.resume_at_bytes) > 0.0:
                unconfirmed_out_of_space = 0
                continue
            unconfirmed_out_of_space += 1
            if unconfirmed_out_of_space >= MAX_UNCONFIRMED_OUT_OF_SPACE_RETRIES:
                raise
            continue
        if not guard.is_low():
            return result
        if on_low_space_outcome is not None:
            on_low_space_outcome()
        if not is_failure(result):
            return result
        logger.bind(recovery=True).warning(
            "Pipeline step failed while disk space was low; retrying it once space is free"
        )


__all__ = [
    "GIB",
    "MAX_UNCONFIRMED_OUT_OF_SPACE_RETRIES",
    "PAUSE_BELOW_FREE_BYTES",
    "RESUME_AT_FREE_BYTES",
    "StorageGuard",
    "VolumeShortfall",
    "default_storage_guard",
    "is_out_of_space",
    "run_step_with_storage_guard",
]
