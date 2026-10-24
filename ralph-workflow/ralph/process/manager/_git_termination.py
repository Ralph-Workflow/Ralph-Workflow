"""Git-aware SIGTERM-to-SIGKILL grace for process termination.

Git removes its ``*.lock`` files from a SIGTERM handler. SIGKILL cannot be
handled, so a kill that follows SIGTERM by microseconds (``grace_period_s=0``)
races git's cleanup and, under CPU load, loses: the lock is orphaned and every
later git call on that repository fails. Every termination path funnels through
the process manager, so the floor lives here once instead of at each call site.
"""

from __future__ import annotations

import importlib
import os
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING, cast

from ralph.timeout_defaults import GIT_TERMINATE_GRACE_SECONDS

if TYPE_CHECKING:
    from collections.abc import Iterable

    from ralph.process.manager._process_manager_types import _PsutilModuleLike


def _psutil() -> _PsutilModuleLike | None:
    try:
        return cast(
            "_PsutilModuleLike", importlib.import_module("psutil")
        )  # cast-policy: seam: structural boundary (sqlite Row / lazy module attr / protocol conferee)
    except ImportError:
        return None


def _psutil_process_name(pid: int) -> str:
    """Return the process name via psutil where there is no ``/proc`` (macOS)."""
    module = _psutil()
    if module is None:
        return ""
    with suppress(Exception):
        return module.Process(pid).name().lower()
    return ""


def _process_name(pid: int) -> str:
    """Return the lower-cased executable name of ``pid``, or "" when unreadable/gone."""
    with suppress(OSError):
        # filesystem-read-ok: /proc is a virtual kernel process-metadata source, not workspace content
        return Path(f"/proc/{pid}/comm").read_text(encoding="utf-8").strip().lower()
    return _psutil_process_name(pid)


def is_git_name(name: str) -> bool:
    """``git`` itself or one of its ``git-*`` helpers, not look-alikes (gitlab-runner)."""
    return name == "git" or name.startswith("git-")


def is_git_pid(pid: int) -> bool:
    """Return True when ``pid`` is a git client (``git``, ``git-remote-https``, ...).

    Both the process name and the executable's own name are consulted, so a
    wrapper-renamed git binary is still recognised where ``/proc`` allows.
    """
    if is_git_name(_process_name(pid)):
        return True
    with suppress(OSError):
        return is_git_name(Path(f"/proc/{pid}/exe").readlink().name.lower())
    return False


def git_aware_grace(grace_period_s: float, pids: Iterable[int]) -> float:
    """Return ``grace_period_s``, raised to the git cleanup window if git is among ``pids``."""
    if grace_period_s >= GIT_TERMINATE_GRACE_SECONDS:
        return grace_period_s
    if any(is_git_pid(pid) for pid in pids):
        return GIT_TERMINATE_GRACE_SECONDS
    return grace_period_s


def _psutil_group_pids(pgid: int) -> list[int]:
    """Group membership via psutil where there is no ``/proc`` (macOS)."""
    module = _psutil()
    members: list[int] = []
    if module is None:
        return members
    with suppress(Exception):
        for proc in module.process_iter(["pid"]):
            with suppress(Exception):
                if os.getpgid(proc.pid) == pgid and proc.status() != "zombie":
                    members.append(proc.pid)
    return members


def group_pids(pgid: int) -> list[int]:
    """Return the live (non-zombie) pids in process group ``pgid``.

    Reads ``/proc`` on Linux; elsewhere falls back to psutil. Used to decide
    whether a group contains git before it is torn down, and to wait for it.
    """
    proc_root = Path("/proc")
    # filesystem-read-ok: /proc directory detection selects the Linux procfs reader before the macOS psutil fallback
    if not proc_root.is_dir():
        return _psutil_group_pids(pgid)
    members: list[int] = []
    # filesystem-read-ok: enumerate virtual procfs process entries; this is not workspace traversal
    for entry in proc_root.iterdir():
        if not entry.name.isdigit():
            continue
        with suppress(OSError, IndexError, ValueError):
            # filesystem-read-ok: virtual procfs process-group metadata
            fields = (entry / "stat").read_text(encoding="utf-8").rsplit(")", 1)[1].split()
            if int(fields[2]) == pgid and fields[0] not in ("Z", "X"):
                members.append(int(entry.name))
    return members
