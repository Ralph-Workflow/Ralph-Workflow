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
        return cast("_PsutilModuleLike", importlib.import_module("psutil"))
    except ImportError:
        return None


def _psutil_process_name(pid: int) -> str:
    module = _psutil()
    if module is None:
        return ""
    with suppress(Exception):
        return module.Process(pid).name().lower()
    return ""


def _process_name(pid: int) -> str:
    with suppress(OSError):
        return Path(f"/proc/{pid}/comm").read_text(encoding="utf-8").strip().lower()
    return _psutil_process_name(pid)


def is_git_name(name: str) -> bool:
    return name == "git" or name.startswith("git-")


def is_git_pid(pid: int) -> bool:
    if is_git_name(_process_name(pid)):
        return True
    with suppress(OSError):
        return is_git_name(Path(f"/proc/{pid}/exe").readlink().name.lower())
    return False


def git_aware_grace(grace_period_s: float, pids: Iterable[int]) -> float:
    if grace_period_s >= GIT_TERMINATE_GRACE_SECONDS:
        return grace_period_s
    if any(is_git_pid(pid) for pid in pids):
        return GIT_TERMINATE_GRACE_SECONDS
    return grace_period_s


def _psutil_group_pids(pgid: int) -> list[int]:
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
    proc_root = Path("/proc")
    if not proc_root.is_dir():
        return _psutil_group_pids(pgid)
    members: list[int] = []
    for entry in proc_root.iterdir():
        if not entry.name.isdigit():
            continue
        with suppress(OSError, IndexError, ValueError):
            fields = (entry / "stat").read_text(encoding="utf-8").rsplit(")", 1)[1].split()
            if int(fields[2]) == pgid and fields[0] not in ("Z", "X"):
                members.append(int(entry.name))
    return members
