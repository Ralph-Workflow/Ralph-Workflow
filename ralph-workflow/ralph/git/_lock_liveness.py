"""Bounded liveness probe for a git lock file.

The probe aggregates the per-process verdicts of
:mod:`ralph.git._lock_proc_inspect` into a single lock verdict and owns the
bounded worker pool the probe runs in. The external (``lsof`` / ``fuser``)
corroboration and the fail-closed entry point stay in
:mod:`ralph.git._lock_recovery`, which owns the bounded subprocess calls.

Split out of ``ralph.git._lock_recovery`` to stay within the repo-structure
line limits; the public surface is unchanged and re-exported from there.
"""

from __future__ import annotations

import atexit
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from ralph.git._lock_proc_inspect import _check_pid_liveness, _list_pids

#: Wall-clock bound on one liveness probe. The scan itself is cheap (a few
#: reads per process); the bound exists for a process table that is huge or a
#: /proc entry that hangs, and is generous because the probe only runs after
#: a lock has already blocked git for at least the staleness floor.
_PROBE_TIMEOUT_S = 5.0
_PROBE_MAX_WORKERS: int = 4
PROBE_MAX_WORKERS: int = _PROBE_MAX_WORKERS

#: Thread-name prefix of the bounded probe pool. It is a named constant so a
#: test can observe the pool's worker count through ``threading.enumerate()``
#: instead of reaching into a private attribute of the executor object.
_PROBE_THREAD_NAME_PREFIX = "ralph-git-lock-probe"


@dataclass(frozen=True)
class ProbeExecutorStatus:
    """Status and health inspection snapshot of the bounded lock-probe thread pool."""

    max_workers: int
    worker_count: int
    is_initialized: bool

    @property
    def capacity(self) -> int:
        """Maximum worker capacity of the probe thread pool."""
        return self.max_workers

    @property
    def healthy(self) -> bool:
        """True if worker thread count does not exceed maximum pool capacity."""
        return self.worker_count <= self.max_workers


class _ProbeExecutorHolder:
    """Dedicated bounded ThreadPoolExecutor holder for lock liveness probes.

    Sizing is bounded to prevent thread accumulation under repeated timeouts.
    """

    def __init__(self, max_workers: int = _PROBE_MAX_WORKERS) -> None:
        self._max_workers = max_workers
        self._executor: ThreadPoolExecutor | None = None
        self._lock = threading.Lock()

    def get(self) -> ThreadPoolExecutor:
        if self._executor is None:
            with self._lock:
                if self._executor is None:
                    self._executor = ThreadPoolExecutor(
                        max_workers=self._max_workers,
                        thread_name_prefix=_PROBE_THREAD_NAME_PREFIX,
                    )
        return self._executor

    def reset(self) -> None:
        with self._lock:
            if self._executor is not None:
                self._executor.shutdown(wait=False, cancel_futures=True)
                self._executor = None

    def status(self) -> ProbeExecutorStatus:
        with self._lock:
            if self._executor is None:
                return ProbeExecutorStatus(
                    max_workers=self._max_workers,
                    worker_count=0,
                    is_initialized=False,
                )
            count = sum(
                1 for t in threading.enumerate() if t.name.startswith(_PROBE_THREAD_NAME_PREFIX)
            )
            return ProbeExecutorStatus(
                max_workers=self._max_workers,
                worker_count=count,
                is_initialized=True,
            )


_probe_pool = _ProbeExecutorHolder()


def probe_pool_capacity() -> int:
    """Return the bounded maximum worker capacity of the probe thread pool."""
    return _PROBE_MAX_WORKERS


def probe_executor_status() -> ProbeExecutorStatus:
    """Return health and status inspection for the probe thread pool."""
    return _probe_pool.status()


def _get_probe_executor() -> ThreadPoolExecutor:
    return _probe_pool.get()


def reset_probe_executor() -> None:
    """Test seam and interpreter-exit cleanup for the probe executor."""
    _probe_pool.reset()


_reset_probe_executor = reset_probe_executor


atexit.register(reset_probe_executor)


def _normalize_external_liveness(
    status: Literal["live", "dead", "unknown"] | bool,
) -> Literal["live", "dead", "unknown"]:
    if status in (True, "live"):
        return "live"
    if status == "dead":
        return "dead"
    return "unknown"


def _scan_proc(
    proc_root: Path,
    lock: Path,
    live_cwds: set[Path],
    common_worktrees_dir: Path,
    gitdir_bytes: bytes,
    gitdir_common_bytes: bytes,
    *,
    lock_stat: os.stat_result | None = None,
    deadline: float | None = None,
) -> Literal["live", "dead", "unknown"]:
    pids = _list_pids(proc_root)
    try:
        lock_resolved = str(lock.resolve())
    except OSError:
        return "unknown"
    if not pids:
        return "unknown"

    has_unknown = False
    lock_uid = lock_stat.st_uid if lock_stat is not None else None
    lock_ident = (lock_stat.st_dev, lock_stat.st_ino) if lock_stat is not None else None
    lock_mtime = lock_stat.st_mtime if lock_stat is not None else None

    for pid in pids:
        if deadline is not None and time.monotonic() >= deadline:
            return "unknown"
        status = _check_pid_liveness(
            proc_root,
            str(pid),
            lock_resolved,
            live_cwds,
            common_worktrees_dir,
            gitdir_bytes,
            gitdir_common_bytes,
            lock_uid,
            lock_ident,
            lock_mtime,
        )
        if status == "live":
            return "live"
        if status == "unknown":
            has_unknown = True

    return "unknown" if has_unknown else "dead"


def _resolve_repo_identity(
    lock_path: Path | str,
    gitdir: Path | str,
    gitdir_common: Path | str | None,
) -> tuple[Path, set[Path], Path, bytes, bytes]:
    """Resolve lock path, live cwds, common worktrees dir, and gitdir byte encodings.

    Raises OSError on any metadata discovery or file read error to fail closed.
    """
    lock = Path(lock_path)
    gitdir_path = Path(gitdir).resolve()
    if gitdir_common is not None:
        gitdir_common_path = Path(gitdir_common).resolve()
    else:
        commondir_file = gitdir_path / "commondir"
        if commondir_file.is_file():
            rel = commondir_file.read_text().strip()
            if not rel:
                raise OSError("Empty commondir file")
            gitdir_common_path = (gitdir_path / rel).resolve()
        elif gitdir_path.parent.name == "worktrees":
            gitdir_common_path = gitdir_path.parent.parent.resolve()
        else:
            gitdir_common_path = gitdir_path

    worktree_top: Path | None = None
    gitdir_file = gitdir_path / "gitdir"
    if gitdir_file.is_file():
        wt_dot_git_str = gitdir_file.read_text().strip()
        if not wt_dot_git_str:
            raise OSError("Empty gitdir file")
        wt_dot_git = Path(wt_dot_git_str).resolve()
        worktree_top = wt_dot_git.parent

    if worktree_top is None and gitdir_path.name == ".git":
        worktree_top = gitdir_path.parent

    main_repo_top: Path | None = (
        gitdir_common_path.parent if gitdir_common_path.name == ".git" else None
    )

    live_cwds: set[Path] = {gitdir_path, gitdir_common_path}
    if worktree_top is not None:
        live_cwds.add(worktree_top)
    if main_repo_top is not None:
        live_cwds.add(main_repo_top)

    common_worktrees_dir = gitdir_common_path / "worktrees"
    # Every linked worktree shares the refs/packed-refs/config locks of the common
    # dir, so a git running in ANY of them can be the holder.
    with suppress(OSError):
        for linked in common_worktrees_dir.glob("*/gitdir"):
            top = linked.read_text(encoding="utf-8").strip()
            if top:
                live_cwds.add(Path(top).resolve().parent)
    gitdir_bytes = str(gitdir_path).encode()
    gitdir_common_bytes = str(gitdir_common_path).encode()

    return lock, live_cwds, common_worktrees_dir, gitdir_bytes, gitdir_common_bytes


_HOST_INIT_MAX_START_S = 600.0


def _pid1_started_at_boot() -> bool:
    """Return True when pid 1 is a host init that started within minutes of boot."""
    try:
        stat_text = Path(
            "/proc/1/stat"
        ).read_text(
            encoding="utf-8"
        )  # filesystem-read-ok: /proc is a virtual kernel process-metadata source, not workspace content
        start_ticks = int(stat_text.rsplit(")", 1)[1].split()[19])
        return start_ticks / os.sysconf("SC_CLK_TCK") < _HOST_INIT_MAX_START_S
    except (OSError, IndexError, ValueError):
        return False


def _not_host_pid_namespace() -> bool:
    """Return True when ``/proc`` may show only part of the machine.

    Inside a container, bwrap or ``unshare --pid`` sandbox, ``/proc`` lists only
    that namespace's processes, so "nobody else holds the lock" proves nothing
    about a git running on the host against a bind-mounted repository. ``NSpid``
    only reveals a namespace nested below the *reader's own*, which a fresh
    ``--mount-proc`` hides, so the telling signs are used instead: a pid 1 that
    is not a boot-time init, a container marker file, or a non-identity uid map.
    """
    try:
        for line in (
            Path("/proc/self/status")
            .read_text(  # filesystem-read-ok: /proc is a virtual kernel process-metadata source, not workspace content
                encoding="utf-8"
            )
            .splitlines()
        ):
            if line.startswith("NSpid:") and len(line.split()[1:]) > 1:
                return True
        if (
            Path(
                "/.dockerenv"
            ).exists()  # filesystem-read-ok: system container marker probe outside workspace
            or Path(
                "/run/.containerenv"
            ).exists()  # filesystem-read-ok: system container marker probe outside workspace
        ):
            return True
        uid_map = (
            Path("/proc/self/uid_map")
            .read_text(  # filesystem-read-ok: /proc is a virtual kernel process-metadata source, not workspace content
                encoding="utf-8"
            )
            .split()
        )
        if uid_map[:3] != ["0", "0", "4294967295"]:
            return True
    except (OSError, IndexError):
        return True
    return not _pid1_started_at_boot()


def _combine_verdicts(
    proc_status: Literal["live", "dead", "unknown"],
    external: Literal["live", "dead", "unknown"],
    *,
    has_proc: bool,
) -> Literal["live", "dead", "unknown"]:
    """Merge the /proc scan with the lsof/fuser corroboration.

    Any "live" wins. A "dead" needs the /proc scan to have proven it; only on a
    platform with no /proc (macOS) is lsof/fuser's "nothing holds it" accepted,
    because there it is the only mechanism there is.
    """
    if "live" in (proc_status, external):
        return "live"
    if proc_status == "dead" or (not has_proc and external == "dead"):
        return "dead"
    return "unknown"
