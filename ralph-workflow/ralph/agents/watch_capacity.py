from __future__ import annotations

import os
import platform
import threading
import time
from collections import OrderedDict
from pathlib import Path
from queue import Empty, Queue
from typing import TYPE_CHECKING, Protocol

from loguru import logger

if TYPE_CHECKING:
    from collections.abc import Callable

_WATCH_LIMIT_PATH = Path("/proc/sys/fs/inotify/max_user_watches")

_PROC_PATH = Path("/proc")

PREDICTION_REUSE_SECONDS = 600.0

_MAX_REMEMBERED_WORKSPACES = 64

_PREDICTIONS: OrderedDict[str, tuple[float, bool]] = OrderedDict()
_PREDICTION_LOCK = threading.Lock()

__all__ = [
    "CAPACITY_PROBE_BUDGET_SECONDS",
    "PREDICTION_REUSE_SECONDS",
    "DirectoryCounter",
    "call_within_budget",
    "process_inotify_watch_count",
    "watch_capacity_is_predicted",
]


class DirectoryCounter(Protocol):
    def __call__(self, workspace: Path, *, cap: int) -> int | None: ...


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


def _count_watchable_directories(
    workspace: Path, cap: int, deadline: float | None = None
) -> int | None:
    directory_count = 0
    try:
        for _root, directories, _files in os.walk(workspace):
            directory_count += 1
            if directory_count >= cap:
                return None
            if deadline is not None and time.monotonic() >= deadline:
                return None
            directories.sort()
    except OSError:
        return None
    return directory_count


CAPACITY_PROBE_BUDGET_SECONDS = 10.0

PROCESS_WATCH_BUDGET = 50_000

_PROBE_THREAD_NAME = "ralph-watch-capacity-probe"


class _ProbeFailed:
    pass


def call_within_budget[T](probe: Callable[[], T], fallback: T, budget_seconds: float) -> T:
    publication: Queue[T | _ProbeFailed] = Queue(maxsize=1)

    def _run_probe() -> None:
        try:
            publication.put(probe())
        except (OSError, RuntimeError, ValueError, AttributeError, TypeError):
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
    measures_host = host_budget is None and directory_counter is None and live_watch_total is None
    key = str(workspace.absolute())
    now = time.monotonic()
    if measures_host:
        with _PREDICTION_LOCK:
            cached = _PREDICTIONS.get(key)
        if cached is not None and now - cached[0] < PREDICTION_REUSE_SECONDS:
            return cached[1]
    deadline = now + budget_seconds

    def bounded_counter(workspace: Path, *, cap: int) -> int | None:
        return _count_watchable_directories(workspace, cap, deadline)

    counter: DirectoryCounter = directory_counter or bounded_counter
    verdict = call_within_budget(
        lambda: _predict_watch_capacity(workspace, host_budget, counter, live_watch_total),
        fallback=True,
        budget_seconds=budget_seconds,
    )
    if measures_host:
        with _PREDICTION_LOCK:
            _PREDICTIONS[key] = (now, verdict)
            _PREDICTIONS.move_to_end(key)
            while len(_PREDICTIONS) > _MAX_REMEMBERED_WORKSPACES:
                _PREDICTIONS.popitem(last=False)
    return verdict


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
    walk_cap = min(budget, PROCESS_WATCH_BUDGET) + 1
    counted_directories = counter(workspace, cap=walk_cap)
    if counted_directories is None or counted_directories > PROCESS_WATCH_BUDGET:
        return True
    return wd_total + counted_directories >= budget


def process_inotify_watch_count(proc_self: Path = _PROC_PATH / "self") -> int | None:
    if not _is_linux_host():
        return None
    total = 0
    try:
        descriptors = list((proc_self / "fd").iterdir())
    except OSError:
        return None
    for descriptor in descriptors:
        try:
            if str(descriptor.readlink()) != "anon_inode:inotify":
                continue
            info = (proc_self / "fdinfo" / descriptor.name).read_text()
        except OSError:
            continue
        total += sum(1 for line in info.splitlines() if line.startswith("inotify "))
    return total
