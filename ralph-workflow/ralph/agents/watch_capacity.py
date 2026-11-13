from __future__ import annotations

import importlib
import os
import platform
import threading
from pathlib import Path
from queue import Empty, Queue
from typing import TYPE_CHECKING, Protocol, cast, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Callable

_WATCH_LIMIT_PATH = Path("/proc/sys/fs/inotify/max_user_watches")

_PROC_PATH = Path("/proc")

__all__ = [
    "CAPACITY_PROBE_BUDGET_SECONDS",
    "DirectoryCounter",
    "call_within_budget",
    "watch_capacity_is_predicted",
]


class DirectoryCounter(Protocol):
    def __call__(self, workspace: Path, *, cap: int) -> int | None: ...


@runtime_checkable
class _DebugLogger(Protocol):
    def opt(self, *, exception: bool) -> _DebugLogger: ...

    def debug(self, message: str) -> object: ...


def _debug_logger() -> _DebugLogger:
    raw: object = cast("object", importlib.import_module("loguru").logger)
    if not isinstance(raw, _DebugLogger):
        raise TypeError("the loguru logger does not provide the debug logger interface")
    return raw


def _is_linux_host() -> bool:
    return platform.system() == "Linux"


def _read_inotify_watch_limit() -> int | None:
    if _is_linux_host():
        try:
            ceiling = _WATCH_LIMIT_PATH.read_text()
            return int(ceiling.strip())
        except OSError:
            return None
    return None


def _read_inotify_watch_user_total() -> int | None:
    if _is_linux_host():
        try:
            user_id = os.getuid()
            watch_total = 0
            for process in _PROC_PATH.iterdir():
                if not process.name.isdigit() or process.stat().st_uid != user_id:
                    continue
                for fdinfo in (process / "fdinfo").iterdir():
                    with fdinfo.open() as stream:
                        watch_total += sum(line.startswith("wd:") for line in stream)
        except OSError:
            return None
        return watch_total
    return None


def _count_watchable_directories(workspace: Path, cap: int) -> int | None:
    directory_count = 0
    try:
        for _root, directories, _files in os.walk(workspace):
            directory_count += 1
            if directory_count >= cap:
                return None
            directories.sort()
    except OSError:
        return None
    return directory_count


CAPACITY_PROBE_BUDGET_SECONDS = 10.0

_PROBE_THREAD_NAME = "ralph-watch-capacity-probe"


class _ProbeFailed:
    pass


def call_within_budget[T](probe: Callable[[], T], fallback: T, budget_seconds: float) -> T:
    publication: Queue[T | _ProbeFailed] = Queue(maxsize=1)

    def _run_probe() -> None:
        try:
            publication.put(probe())
        except Exception:
            logger = _debug_logger()
            logger.opt(exception=True).debug("workspace watch capacity probe failed")
            publication.put(_ProbeFailed())

    worker = threading.Thread(target=_run_probe, name=_PROBE_THREAD_NAME, daemon=True)
    worker.start()
    try:
        answer = publication.get(timeout=budget_seconds)
    except Empty:
        return fallback
    if isinstance(answer, _ProbeFailed):
        return fallback
    return answer


def watch_capacity_is_predicted(
    workspace: Path,
    host_budget: int | None,
    directory_counter: DirectoryCounter | None,
    live_watch_total: int | None,
    budget_seconds: float = CAPACITY_PROBE_BUDGET_SECONDS,
) -> bool:
    return call_within_budget(
        lambda: _predict_watch_capacity(
            workspace, host_budget, directory_counter, live_watch_total
        ),
        fallback=True,
        budget_seconds=budget_seconds,
    )


def _predict_watch_capacity(
    workspace: Path,
    host_budget: int | None,
    directory_counter: DirectoryCounter | None,
    live_watch_total: int | None,
) -> bool:
    budget = host_budget if host_budget is not None else _read_inotify_watch_limit()
    if budget is None:
        return False
    wd_total = (
        live_watch_total
        if live_watch_total is not None
        else (_read_inotify_watch_user_total() or 0)
    )
    counter = directory_counter or _count_watchable_directories
    counted_directories = counter(workspace, cap=budget + 1)
    workspace_dir_count = budget + 1 if counted_directories is None else counted_directories
    return wd_total + workspace_dir_count >= budget
