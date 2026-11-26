from __future__ import annotations

import os
import signal
import time
from pathlib import Path
from typing import TYPE_CHECKING

import psutil
from loguru import logger

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

__all__ = ["reap_session_orphans"]

_SESSION_ENV = b"MCP_SESSION_ID="
_OWNER_ENV = b"RALPH_MCP_OWNER_PID="
_POLL_SECONDS = 0.1


def _default_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _pids(proc_root: Path) -> list[int]:
    try:
        return [int(entry.name) for entry in proc_root.iterdir() if entry.name.isdigit()]
    except OSError:
        return []


def _carries_session(proc_root: Path, pid: int, session: bytes, owner: bytes) -> bool:
    try:
        variables = (proc_root / str(pid) / "environ").read_bytes().split(b"\0")
    except OSError:
        return False
    if session not in variables:
        return False
    owners = [variable for variable in variables if variable.startswith(_OWNER_ENV)]
    return not owners or owner in owners


def _parent_of(proc_root: Path, pid: int) -> int | None:
    try:
        stat = (proc_root / str(pid) / "stat").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    fields = stat.rpartition(")")[2].split()
    return int(fields[1]) if len(fields) > 1 and fields[1].isdigit() else None


def _psutil_process_table(
    session_id: str, owner_pid: int
) -> tuple[dict[int, int | None], list[int]]:
    parents: dict[int, int | None] = {}
    roots: list[int] = []
    wanted_owner = str(owner_pid)
    for process in psutil.process_iter():
        try:
            pid = process.pid
            parents[pid] = process.ppid()
            environment = process.environ()
        except (psutil.Error, OSError):
            continue
        if (
            environment.get("MCP_SESSION_ID") == session_id
            and environment.get("RALPH_MCP_OWNER_PID", wanted_owner) == wanted_owner
        ):
            roots.append(pid)
    return parents, roots


def _with_descendants(roots: Iterable[int], parents: dict[int, int | None]) -> set[int]:
    children: dict[int, list[int]] = {}
    for pid, parent in parents.items():
        if parent is not None:
            children.setdefault(parent, []).append(pid)
    found: set[int] = set()
    pending = list(roots)
    while pending:
        pid = pending.pop()
        if pid in found:
            continue
        found.add(pid)
        pending.extend(children.get(pid, ()))
    return found


def _protected(parents: dict[int, int | None]) -> set[int]:
    protected: set[int] = set()
    pid: int | None = os.getpid()
    while pid is not None and pid > 1 and pid not in protected:
        protected.add(pid)
        pid = parents.get(pid)
    return protected


def reap_session_orphans(
    session_id: str,
    *,
    owner_pid: int,
    proc_root: Path = Path("/proc"),
    signal_fn: Callable[[int, int], None] = os.kill,
    alive_fn: Callable[[int], bool] = _default_alive,
    sleep: Callable[[float], None] = time.sleep,
    grace_seconds: float = 5.0,
) -> int:
    if not session_id:
        return 0
    if proc_root.is_dir():
        session = _SESSION_ENV + session_id.encode()
        owner = _OWNER_ENV + str(owner_pid).encode()
        pids = _pids(proc_root)
        parents = {pid: _parent_of(proc_root, pid) for pid in pids}
        roots = [pid for pid in pids if _carries_session(proc_root, pid, session, owner)]
    else:
        parents, roots = _psutil_process_table(session_id, owner_pid)
    targets = _with_descendants(roots, parents) - _protected(parents)
    if not targets:
        return 0
    for pid in sorted(targets):
        try:
            signal_fn(pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            continue
    waited = 0.0
    survivors = [pid for pid in targets if alive_fn(pid)]
    while survivors and waited < grace_seconds:
        sleep(_POLL_SECONDS)
        waited += _POLL_SECONDS
        survivors = [pid for pid in survivors if alive_fn(pid)]
    for pid in survivors:
        try:
            signal_fn(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            continue
    logger.warning(
        "reaped {} process(es) left running by ended agent session {} ({} needed SIGKILL)",
        len(targets),
        session_id,
        len(survivors),
    )
    return len(targets)
