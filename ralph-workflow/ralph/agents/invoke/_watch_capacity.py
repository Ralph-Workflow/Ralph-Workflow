"""Deciding whether the host can afford another recursive workspace watch.

Split out of :mod:`ralph.agents.invoke._workspace` so the probe that
answers that question -- and the time bound that keeps it from parking
an agent launch -- read as one thing.

The probe runs on the way into ``WorkspaceMonitor.start()``, which
``invoke_agent`` calls AFTER logging the agent's argv and BEFORE
spawning the agent process. Nothing here is required for the agent to
run, and everything here can be slow: the live inotify total comes from
a sweep of ``/proc/<pid>/fdinfo`` and the workspace's directory count
from ``os.walk``. Neither has a bound of its own, so both run under one
that fails toward "do not watch".
"""

from __future__ import annotations

import os
import platform
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from loguru import logger

if TYPE_CHECKING:
    from collections.abc import Callable

#: Linux's per-real-user inotify-watch ceiling.
_WATCH_LIMIT_PATH = Path("/proc/sys/fs/inotify/max_user_watches")

#: The kernel process tree, swept for this user's live watch total.
_PROC_PATH = Path("/proc")

__all__ = [
    "CAPACITY_PROBE_BUDGET_SECONDS",
    "DirectoryCounter",
    "call_within_budget",
    "watch_capacity_is_predicted",
]


class DirectoryCounter(Protocol):
    """Structural contract for bounded recursive directory counters."""

    def __call__(self, workspace: Path, *, cap: int) -> int | None: ...


def _is_linux_host() -> bool:
    """Return whether the running host exposes Linux's procfs inotify data."""
    return platform.system() == "Linux"


def _read_inotify_watch_limit() -> int | None:
    """Return Linux's per-real-user inotify-watch ceiling when available."""
    if _is_linux_host():
        try:
            # filesystem-read-ok: a kernel sysctl, not workspace content
            ceiling = _WATCH_LIMIT_PATH.read_text()
            return int(ceiling.strip())
        except OSError:
            return None
    return None


def _read_inotify_watch_user_total() -> int | None:
    """Return the current real user's live inotify-watch count on Linux."""
    if _is_linux_host():
        try:
            user_id = os.getuid()
            watch_total = 0
            for process in _PROC_PATH.iterdir():  # filesystem-read-ok: kernel tree, not content
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
    """Count workspace directories, returning None once ``cap`` is reached."""
    directory_count = 0
    try:
        # filesystem-read-ok: bounded capacity counter, before a Workspace exists
        for _root, directories, _files in os.walk(workspace):
            directory_count += 1
            if directory_count >= cap:
                return None
            directories.sort()
    except OSError:
        return None
    return directory_count


#: How long the capacity probe may take before the monitor gives up on
#: watching. The probe sweeps ``/proc/<pid>/fdinfo`` for the host's live
#: inotify total and walks the workspace for its directory count, and
#: neither has a bound of its own: a hung network mount inside the
#: workspace, or one process in uninterruptible sleep, makes them never
#: return. That would be survivable anywhere else, but this runs between
#: ``invoke_agent``'s "Invoking agent" log line and the agent spawn, so
#: an unbounded probe parks the whole run before any process exists for
#: a watchdog to time out. Generous enough for a real walk of a large
#: repository; finite is the point.
CAPACITY_PROBE_BUDGET_SECONDS = 10.0


def call_within_budget[T](probe: Callable[[], T], fallback: T, budget_seconds: float) -> T:
    """Run ``probe``, returning ``fallback`` if it does not answer in time.

    What the abandoned call leaves behind depends on what it was. A
    capacity probe is a read-only walk holding no lock, and costs an
    idle thread. A watch start that lands after its budget leaves a LIVE
    observer -- an inotify tree and an emitter thread this process no
    longer tracks; see ``_workspace.WorkspaceMonitor._abandon_slow_watch_start``
    for what that means. Both are paid in preference to blocking the
    run, which is what the caller is here to avoid.
    """
    answer: list[T] = []

    def _run_probe() -> None:
        try:
            answer.append(probe())
        except Exception:
            logger.opt(exception=True).debug("workspace watch capacity probe failed")

    worker = threading.Thread(target=_run_probe, name="ralph-watch-capacity-probe", daemon=True)
    worker.start()
    worker.join(budget_seconds)
    if answer:
        return answer[0]
    logger.warning(
        "Workspace watch capacity probe did not answer within {}s; "
        "starting the agent without workspace monitoring",
        budget_seconds,
    )
    return fallback


def watch_capacity_is_predicted(
    workspace: Path,
    host_budget: int | None,
    directory_counter: DirectoryCounter | None,
    live_watch_total: int | None,
    budget_seconds: float = CAPACITY_PROBE_BUDGET_SECONDS,
) -> bool:
    """Return whether another recursive workspace watch would exhaust the budget.

    Fails toward ``True`` -- "assume it would" -- when the probe cannot
    answer inside ``budget_seconds``. Not watching costs the idle
    watchdog one evidence channel; not returning costs the run.
    """
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
    """Answer the capacity question, taking as long as the host makes it take."""
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
