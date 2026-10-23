"""Git stale lock recovery and contention handling.

Extracted from ``ralph.git.operations`` to stay within repo-structure line limits.
Provides an age-and-liveness gate to safely unlink stale Git lock files,
along with bounded backoff retry for transient contention.
"""

from __future__ import annotations

import atexit
import errno
import os
import re
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from collections.abc import Callable
    from typing import Protocol

    class LivenessProbeFn(Protocol):
        def __call__(
            self,
            lock_path: Path,
            gitdir: Path,
        ) -> Literal["live", "dead", "unknown"]: ...


from git.exc import GitCommandError
from loguru import logger

from ralph.git.errors import GitOperationError

_LOCK_PATH_PATTERN = re.compile(r"Unable to create '([^']+\.lock)'")
_RECOVERABLE_GIT_LOCK_FILES = frozenset({"index.lock", "HEAD.lock", "packed-refs.lock"})
_STALE_GIT_LOCK_AGE_SECONDS = 10.0
_PROBE_TIMEOUT_S = 1.0
_PROBE_MAX_WORKERS: int = 4


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
                        thread_name_prefix="ralph-git-lock-probe",
                    )
        return self._executor

    def reset(self) -> None:
        with self._lock:
            if self._executor is not None:
                self._executor.shutdown(wait=False, cancel_futures=True)
                self._executor = None


_probe_pool = _ProbeExecutorHolder()


def _get_probe_executor() -> ThreadPoolExecutor:
    return _probe_pool.get()


def _reset_probe_executor() -> None:
    """Test seam and interpreter-exit cleanup for the probe executor."""
    _probe_pool.reset()


atexit.register(_reset_probe_executor)

# Bounded backoff constants for git lock contention retry
_LOCK_BACKOFF_S: tuple[float, ...] = (0.2, 0.5, 1.0)  # total ≤ 1.7s

# Seam for time.sleep — patch in tests to avoid real delays
_sleep_fn: Callable[[float], None] = time.sleep


def _parse_lock_path_from_error(error_text: str) -> Path | None:
    match = _LOCK_PATH_PATTERN.search(error_text)
    if match is None:
        return None
    lock_path = Path(match.group(1))
    if lock_path.name in _RECOVERABLE_GIT_LOCK_FILES:
        return lock_path
    if "refs" not in lock_path.parts or not lock_path.name.endswith(".lock"):
        return None
    return lock_path


def _gitdir_for_lock(lock_path: Path) -> Path:
    parts = lock_path.parts
    if "refs" not in parts:
        return lock_path.parent
    refs_index = parts.index("refs")
    return Path(*parts[:refs_index])


def _normalize_external_liveness(
    status: Literal["live", "dead", "unknown"] | bool,
) -> Literal["live", "dead", "unknown"]:
    if status in (True, "live"):
        return "live"
    if status == "dead":
        return "dead"
    return "unknown"


def _probe_lsof_fuser(lock: Path, timeout: float) -> Literal["live", "dead", "unknown"]:
    # mcp-timeout-ok: bounded lock-liveness probe
    try:
        # filesystem-poll-ok: bounded read-only lsof liveness probe
        result = subprocess.run(  # resource-lifecycle-ok: read-only lsof probe with bounded timeout
            ["lsof", "-F", "pL", str(lock)],
            timeout=timeout,
            capture_output=True,
            check=False,
        )
        if result.returncode == 0 and b"p" in result.stdout:
            return "live"
        if result.returncode == 1:
            return "dead"
    except (OSError, subprocess.TimeoutExpired, PermissionError):
        pass

    # mcp-timeout-ok: bounded lock-liveness probe
    try:
        result = (
            # filesystem-poll-ok: bounded read-only fuser liveness probe
            subprocess.run(  # resource-lifecycle-ok: read-only fuser probe with bounded timeout
                ["fuser", str(lock)],
                timeout=timeout,
                capture_output=True,
                check=False,
            )
        )
        if result.returncode == 0:
            return "live"
        if result.returncode == 1:
            return "dead"
    except (OSError, subprocess.TimeoutExpired, PermissionError):
        pass

    return "unknown"


def _is_git_process(proc_root: Path, pid_str: str) -> bool:
    """Return True if the process identified by pid_str is a git process.

    Checks ``/proc/<pid>/comm`` and ``/proc/<pid>/cmdline``. If either source
    indicates the executable name is or contains "git", returns True.
    Raises PermissionError if inspection is denied by permissions.
    """
    comm_path = proc_root / pid_str / "comm"
    try:
        if comm_path.is_file():
            comm = comm_path.read_text(encoding="utf-8", errors="replace").strip().lower()
            if "git" in comm:
                return True
    except PermissionError:
        raise
    except OSError:
        pass

    cmdline_path = proc_root / pid_str / "cmdline"
    try:
        if cmdline_path.is_file():
            cmdline = cmdline_path.read_bytes()
            parts = [p.decode(errors="replace") for p in cmdline.split(b"\x00") if p]
            if parts:
                exe = Path(parts[0]).name.lower()
                if "git" in exe:
                    return True
            elif b"git" in cmdline.lower():
                return True
    except PermissionError:
        raise
    except OSError:
        pass

    return False


def _can_rule_out_non_git_process(proc_root: Path, pid_str: str) -> bool:
    """Return True if we can positively prove the process is NOT a git process."""
    comm_path = proc_root / pid_str / "comm"
    try:
        if comm_path.is_file():
            comm = comm_path.read_text(encoding="utf-8", errors="replace").strip().lower()
            if comm:
                return "git" not in comm
    except (OSError, PermissionError):
        pass

    cmdline_path = proc_root / pid_str / "cmdline"
    try:
        if cmdline_path.is_file():
            cmdline = cmdline_path.read_bytes()
            parts = [p.decode(errors="replace") for p in cmdline.split(b"\x00") if p]
            if parts:
                exe = Path(parts[0]).name.lower()
                return "git" not in exe
    except (OSError, PermissionError):
        pass

    return False


def _is_unreadable_os_error(e: OSError) -> bool:
    return isinstance(e, PermissionError) or e.errno in (errno.EACCES, errno.EPERM)


def _is_dead_process_os_error(e: OSError) -> bool:
    return isinstance(e, (FileNotFoundError, ProcessLookupError)) or e.errno in (
        errno.ENOENT,
        errno.ESRCH,
    )


def _pid_holds_lock_fd(
    pid_dir: Path,
    lock_resolved: str,
) -> Literal["live", "not_live", "unknown"]:
    """Check if process has the lock file open in fd/."""
    try:
        for fd_link in (pid_dir / "fd").iterdir():
            try:
                if str(fd_link.readlink()) == lock_resolved:
                    return "live"
            except OSError as e:
                if _is_unreadable_os_error(e):
                    return "unknown"
        return "not_live"
    except OSError as e:
        if _is_unreadable_os_error(e):
            return "unknown"
        if _is_dead_process_os_error(e):
            return "not_live"
        return "unknown"


def _pid_in_repo_cwd(
    pid_dir: Path,
    live_cwds: set[Path],
    common_worktrees_dir: Path,
) -> Literal["in_repo", "not_in_repo", "unknown"]:
    """Check if process cwd is in the workspace repository or gitdirs.

    A process is treated as "in the repo" when its cwd is ANY path inside the
    repository: the worktree-private gitdir, the common gitdir, the worktree
    working tree, the main-repo working tree, any subdirectory of any of those,
    or any path under ``gitdir_common/worktrees/``. This covers the common case
    of a developer running ``git status`` from a project subdirectory while a
    long-running ``git gc`` holds a worktree-private lock.
    """
    try:
        cwd_link = pid_dir / "cwd"
        if not (cwd_link.is_symlink() or cwd_link.exists()):
            return "not_in_repo"
        cwd_target = cwd_link.readlink()
        try:
            cwd_resolved = cwd_target.resolve()
        except OSError:
            cwd_resolved = cwd_target
        if (
            bool({cwd_target, cwd_resolved} & (live_cwds | {common_worktrees_dir}))
            or str(cwd_target).startswith(str(common_worktrees_dir) + "/")
            or str(cwd_resolved).startswith(str(common_worktrees_dir) + "/")
            or any(
                str(cwd_resolved).startswith(str(cwd) + "/")
                or str(cwd_target).startswith(str(cwd) + "/")
                for cwd in (live_cwds | {common_worktrees_dir})
                if cwd != common_worktrees_dir
            )
        ):
            return "in_repo"
        return "not_in_repo"
    except OSError as e:
        if _is_unreadable_os_error(e):
            return "unknown"
        if _is_dead_process_os_error(e):
            return "not_in_repo"
        return "unknown"


def _resolve_environ_gitdir(pid_dir: Path, raw_val: bytes) -> tuple[Path | None, bool]:
    """Resolve raw GIT_DIR bytes against cwd; returns (resolved_path, resolution_failed)."""
    if not raw_val:
        return None, False
    try:
        val_str = os.fsdecode(raw_val)
        raw_path = Path(val_str)
        if raw_path.is_absolute():
            return raw_path.resolve(), False
        proc_cwd = (pid_dir / "cwd").readlink()
        if not proc_cwd.is_absolute():
            return None, True
        return (proc_cwd / raw_path).resolve(), False
    except (OSError, UnicodeDecodeError):
        return None, True


def _pid_has_gitdir_environ(
    pid_dir: Path,
    gitdir: bytes | Path,
    gitdir_common: bytes | Path,
) -> Literal["matches", "no_match", "unknown"]:
    """Check if process environ sets GIT_DIR matching the repository gitdir.

    Parses exact NUL-separated GIT_DIR=... entries from /proc/<pid>/environ.
    Relative GIT_DIR paths are resolved against the inspected process's cwd
    (/proc/<pid>/cwd via readlink). If resolution cannot be established,
    fails closed to 'unknown'.
    """
    try:
        environ_path = pid_dir / "environ"
        if not environ_path.is_file():
            return "no_match"
        environ_bytes = environ_path.read_bytes()
    except OSError as e:
        if _is_dead_process_os_error(e):
            return "no_match"
        return "unknown"

    try:
        targets = {
            Path(os.fsdecode(g) if isinstance(g, (bytes, bytearray)) else g).resolve()
            for g in (gitdir, gitdir_common)
        }
    except OSError:
        return "unknown"

    resolution_failed = False
    for entry in environ_bytes.split(b"\x00"):
        if entry.startswith(b"GIT_DIR="):
            candidate, failed = _resolve_environ_gitdir(pid_dir, entry[8:])
            if failed:
                resolution_failed = True
            elif candidate is not None and candidate in targets:
                return "matches"

    return "unknown" if resolution_failed else "no_match"


def _check_non_git_liveness(
    fd_status: Literal["live", "not_live", "unknown"],
) -> Literal["live", "dead", "unknown"]:
    """Resolve liveness for confirmed non-git processes.

    Fail-closed: an unreadable fd table (``unknown``) is treated as
    ``unknown`` (refuse) rather than ``dead`` (unlink).
    """
    if fd_status == "live":
        return "live"
    if fd_status == "not_live":
        return "dead"
    return "unknown"


def _is_zombie_process(pid_dir: Path) -> bool:
    """Return True if process has terminated (zombie/dead state in /proc/<pid>/status)."""
    try:
        status_file = pid_dir / "status"
        if status_file.is_file():
            for line in status_file.read_text(encoding="utf-8", errors="replace").splitlines():
                if line.startswith("State:"):
                    state = line.split(":", 1)[1].strip()
                    return state.startswith(("Z", "X")) or "zombie" in state.lower()
    except OSError:
        pass
    return False


def _check_git_liveness(
    proc_root: Path,
    pid_str: str,
    pid_dir: Path,
    live_cwds: set[Path],
    common_worktrees_dir: Path,
    gitdir: bytes | Path,
    gitdir_common: bytes | Path,
) -> Literal["live", "dead", "unknown"]:
    """Resolve liveness for candidate git processes by cwd and environ."""
    cwd_status = _pid_in_repo_cwd(pid_dir, live_cwds, common_worktrees_dir)
    env_status = _pid_has_gitdir_environ(pid_dir, gitdir, gitdir_common)

    if cwd_status == "unknown" or env_status == "unknown":
        return "unknown"

    if cwd_status == "in_repo" or env_status == "matches":
        try:
            if _is_git_process(proc_root, pid_str):
                return "live"
        except PermissionError:
            return "unknown"

    return "dead"


def _check_pid_liveness(
    proc_root: Path,
    pid_str: str,
    lock_resolved: str,
    live_cwds: set[Path],
    common_worktrees_dir: Path,
    gitdir: bytes | Path,
    gitdir_common: bytes | Path,
) -> Literal["live", "dead", "unknown"]:
    """Check liveness signals for a single process PID.

    Returns:
        "live" -- process holds the lock file open (any process) or is a git
                  process with cwd / environ in the repository/gitdir.
        "dead" -- process does not hold the lock and is not a git process in repo.
        "unknown" -- process cannot be inspected (PermissionError etc.).
    """
    pid_dir = proc_root / pid_str

    if _is_zombie_process(pid_dir):
        return "dead"

    is_non_git = _can_rule_out_non_git_process(proc_root, pid_str)

    # (i) fd check - open-fd protection applies to ALL processes
    fd_status = _pid_holds_lock_fd(pid_dir, lock_resolved)
    if is_non_git:
        return _check_non_git_liveness(fd_status)

    if fd_status != "not_live":
        return fd_status

    # (ii) cwd & (iii) environ checks for git processes
    return _check_git_liveness(
        proc_root,
        pid_str,
        pid_dir,
        live_cwds,
        common_worktrees_dir,
        gitdir,
        gitdir_common,
    )


def _scan_proc(
    proc_root: Path,
    lock: Path,
    live_cwds: set[Path],
    common_worktrees_dir: Path,
    gitdir_bytes: bytes,
    gitdir_common_bytes: bytes,
) -> Literal["live", "dead", "unknown"]:
    if not os.access(proc_root, os.R_OK):
        return "unknown"
    try:
        proc_entries = list(proc_root.iterdir())
    except (PermissionError, FileNotFoundError, OSError):
        return "unknown"

    pids: list[int] = [int(entry.name) for entry in proc_entries if entry.name.isdigit()]
    if not pids:
        return "unknown"

    try:
        lock_resolved = str(lock.resolve())
    except OSError:
        return "unknown"

    has_unknown = False

    for pid in pids:
        status = _check_pid_liveness(
            proc_root,
            str(pid),
            lock_resolved,
            live_cwds,
            common_worktrees_dir,
            gitdir_bytes,
            gitdir_common_bytes,
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
    gitdir_bytes = str(gitdir_path).encode()
    gitdir_common_bytes = str(gitdir_common_path).encode()

    return lock, live_cwds, common_worktrees_dir, gitdir_bytes, gitdir_common_bytes


def _lock_has_no_live_holder(
    lock_path: Path | str,
    gitdir: Path | str,
    gitdir_common: Path | str | None = None,
    *,
    probe_timeout_s: float = _PROBE_TIMEOUT_S,
    _proc_root: Path = Path("/proc"),
    probe_lsof_fuser_fn: Callable[
        [Path, float], Literal["live", "dead", "unknown"] | bool
    ]
    | None = None,
    executor: ThreadPoolExecutor | None = None,
) -> Literal["live", "dead", "unknown"]:
    """Return whether the lock has no live holder, or "unknown" if unprovable.

    Liveness signals are OR'd: any single check returning "live" is
    sufficient to refuse lock removal (negative liveness proof).
    The whole probe (including gitdir resolution and metadata reads) runs inside
    a bounded ``ThreadPoolExecutor`` with a strict ``probe_timeout_s`` (0.5 s) budget;
    on ``TimeoutError`` or error, the helper returns "unknown" (fail-closed).

    Liveness OR-predicate (independently sufficient):
      (i) ``/proc/<pid>/fd/*`` contains the lock path
     (ii) ``/proc/<pid>/cwd`` is the per-worktree private gitdir,
          the common gitdir, the worktree top level, the main-repo
          top level, or any path under ``gitdir_common/worktrees/``
    (iii) ``/proc/<pid>/environ`` has ``GIT_DIR`` matching ``gitdir``
          or ``gitdir_common``
     (iv) ``lsof -F pL <lock_path>`` returns 0 with ``p<PID>`` lines
      (v) ``fuser`` fallback

    Returns:
        "live"  -- a live process is holding or using the lock; refuse
        "dead"  -- no live holder found across scanned signals; safe to unlink
        "unknown" -- platform limitation or probe timeout; refuse
    """
    lsof_fuser = probe_lsof_fuser_fn if probe_lsof_fuser_fn is not None else _probe_lsof_fuser

    probe_start = time.monotonic()

    def _probe() -> Literal["live", "dead", "unknown"]:
        try:
            (
                lock,
                live_cwds,
                common_worktrees_dir,
                gitdir_bytes,
                gitdir_common_bytes,
            ) = _resolve_repo_identity(lock_path, gitdir, gitdir_common)
        except OSError as exc:
            if time.monotonic() - probe_start < probe_timeout_s:
                with suppress(Exception):
                    logger.warning(
                        "Lock liveness probe returning 'unknown' for {}: failed to resolve "
                        "repository identity ({}: {}); refusing to unlink without proven "
                        "negative liveness",
                        lock_path,
                        type(exc).__name__,
                        exc,
                    )
            return "unknown"

        external_status: Literal["live", "dead", "unknown"] | bool = "unknown"
        if _proc_root == Path("/proc"):
            external_status = lsof_fuser(lock, probe_timeout_s)
            normalized_external_status = _normalize_external_liveness(external_status)
            if normalized_external_status != "unknown":
                return normalized_external_status

        proc_status = _scan_proc(
            _proc_root,
            lock,
            live_cwds,
            common_worktrees_dir,
            gitdir_bytes,
            gitdir_common_bytes,
        )
        if proc_status in ("live", "dead"):
            return proc_status
        if probe_lsof_fuser_fn is not None:
            external_status = lsof_fuser(lock, probe_timeout_s)
            normalized_external_status = _normalize_external_liveness(external_status)
            if normalized_external_status != "unknown":
                return normalized_external_status
        if time.monotonic() - probe_start >= probe_timeout_s:
            return "unknown"
        platform_reason = (
            "no /proc scan succeeded and lsof/fuser did not return a holder"
            if not _proc_root.exists()
            else "/proc scan reported 'unknown' (likely other-UID or platform "
            "limitation) and lsof/fuser returned no holder"
        )
        with suppress(Exception):
            logger.warning(
                "Lock liveness probe returning 'unknown' for {}: {}; refusing to "
                "unlink without proven negative liveness",
                lock_path,
                platform_reason,
            )
        return "unknown"

    active_executor = executor if executor is not None else _get_probe_executor()
    future = active_executor.submit(_probe)
    try:
        return future.result(timeout=probe_timeout_s)
    except TimeoutError:
        logger.warning(
            "Lock liveness probe returning 'unknown' for {}: probe exceeded "
            "{}s budget; refusing to unlink without proven negative liveness",
            lock_path,
            probe_timeout_s,
        )
        with suppress(AttributeError):
            future.cancel()
        return "unknown"
    except Exception as exc:
        logger.warning(
            "Lock liveness probe returning 'unknown' for {}: probe raised "
            "{}: {}; refusing to unlink without proven negative liveness",
            lock_path,
            type(exc).__name__,
            exc,
        )
        with suppress(AttributeError):
            future.cancel()
        return "unknown"


def _recover_stale_git_lock(
    operation: str,
    error: Exception,
    *,
    liveness_fn: LivenessProbeFn | None = None,
) -> bool:
    """Recover from a transient Git lock-file error using an age-and-liveness gate.

    A lock is removed only when both conditions are met:
      (a) Conservative age check: the lock must be older than
          ``_STALE_GIT_LOCK_AGE_SECONDS`` (10 s). Git lock files do NOT
          record the PID of the holding process (correcting a false PID claim);
          age alone is not proof of staleness, but locks younger than 10s are
          refused removal to protect potentially live operations.
      (b) Negative liveness proof: verified via :func:`_lock_has_no_live_holder`
          using an OR-predicate across multiple process signals (fd table,
          cwd, environ, lsof, fuser). The lock is unlinked ONLY when liveness
          is affirmatively proven "dead". If any check signals "live" or
          liveness is "unknown" (timeout or error), removal is refused fail-closed.

    Returns:
        True  -- lock removed or already gone (caller can retry).
        False -- lock is live, too young, or unprovable (caller
                 surfaces the contention).
    """
    error_text = str(error)
    lock_path = _parse_lock_path_from_error(error_text)
    if lock_path is None:
        return False
    if not lock_path.exists():
        logger.warning(
            "Retrying {} after transient git lock contention; lock path already disappeared: {}",
            operation,
            lock_path,
        )
        return True

    try:
        age_seconds = time.time() - lock_path.stat().st_mtime
        if age_seconds < _STALE_GIT_LOCK_AGE_SECONDS:
            logger.warning(
                "Refusing to auto-unlink git lock for {} (path={}, age={:.1f}s < {}s); "
                "too young to be proven stale.",
                operation,
                lock_path,
                age_seconds,
                _STALE_GIT_LOCK_AGE_SECONDS,
            )
            return False

        gitdir = _gitdir_for_lock(lock_path)
        liveness_check = liveness_fn if liveness_fn is not None else _lock_has_no_live_holder
        liveness = liveness_check(lock_path, gitdir)
        if liveness in {"live", "unknown"}:
            logger.warning(
                "Refusing to auto-unlink git lock for {} (path={}, age={:.1f}s, liveness={}); "
                "fail-closed to protect a potentially live lock.",
                operation,
                lock_path,
                age_seconds,
                liveness,
            )
            return False

        # filesystem-write-ok: stale git lock removal follows age + liveness validation above
        lock_path.unlink()
        logger.warning(
            "Recovered stale git lock for {} by removing {} (liveness=dead)",
            operation,
            lock_path,
        )
        return True
    except OSError:
        return False


def _find_lock_holder(lock_path: Path) -> str:
    """Best-effort discovery of holder PID for operator logging."""
    if not lock_path.exists():
        return "unknown"
    try:
        # mcp-timeout-ok: bounded lock holder discovery
        # filesystem-poll-ok: bounded read-only lsof lock holder discovery
        result = subprocess.run(  # resource-lifecycle-ok: read-only lsof probe with bounded timeout
            ["lsof", "-F", "p", str(lock_path)],
            timeout=_PROBE_TIMEOUT_S,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                if line.startswith("p") and line[1:].isdigit():
                    return f"PID {line[1:]}"
    except (OSError, subprocess.TimeoutExpired, PermissionError):
        pass

    try:
        # mcp-timeout-ok: bounded lock holder discovery
        result = (
            # filesystem-poll-ok: bounded read-only fuser lock holder discovery
            subprocess.run(  # resource-lifecycle-ok: read-only fuser probe with bounded timeout
                ["fuser", str(lock_path)],
                timeout=_PROBE_TIMEOUT_S,
                capture_output=True,
                text=True,
                check=False,
            )
        )
        if result.returncode == 0 and result.stdout.strip():
            pid = result.stdout.strip().split()[0]
            if pid.isdigit():
                return f"PID {pid}"
    except (OSError, subprocess.TimeoutExpired, PermissionError):
        pass

    return "unknown"


def _run_git_operation_with_stale_lock_recovery[T](
    operation: str,
    action: Callable[[], T],
    *,
    recover_fn: Callable[[str, Exception], bool] | None = None,
    sleep_fn: Callable[[float], None] | None = None,
    holder_fn: Callable[[Path], str] | None = None,
) -> T:
    """Run a git action with bounded backoff on persistent lock contention.

    When an operation fails due to lock contention:
      1. Attempts stale-lock recovery (:func:`_recover_stale_git_lock`) using
         the age-and-liveness gate (conservative 10s age + negative liveness proof).
      2. If recovered (e.g. stale lock unlinked or gone), retries immediately.
      3. If not recovered (lock young, live holder, or unknown), logs operator-facing
         message naming lock path, age, and holder (if known, or 'unknown'),
         then sleeps with bounded backoff via ``_sleep_fn(delay)`` and retries.
      4. After exhausting backoff attempts (up to 3 retry attempts: 0.2s, 0.5s, 1.0s,
         total ≤ 1.7s), re-raises the GitCommandError / GitOperationError.
         Upstream callers (e.g. pipeline reducer) may perform in-place retry
         when policy defines no recovery phase.

    Args:
        operation: Human-readable operation name for log messages.
        action: The git operation to perform.
        recover_fn: Optional override for stale lock recovery.
        sleep_fn: Optional override for sleep backoff delay.
        holder_fn: Optional override for lock holder lookup.

    Returns:
        The result of the action on success.

    Raises:
        GitCommandError: The original exception after all retry attempts are exhausted.
        GitOperationError: The original exception if raised and exhausted.
    """
    retry = 0
    max_retries = len(_LOCK_BACKOFF_S)
    stale_recovered_retries = 0
    max_stale_recovered_retries = 3

    recover = recover_fn if recover_fn is not None else _recover_stale_git_lock
    sleep = sleep_fn if sleep_fn is not None else _sleep_fn
    find_holder = holder_fn if holder_fn is not None else _find_lock_holder

    while True:
        try:
            return action()
        except (GitCommandError, GitOperationError) as exc:
            lock_path = _parse_lock_path_from_error(str(exc))
            if lock_path is None:
                raise

            if recover(operation, exc):
                stale_recovered_retries += 1
                if stale_recovered_retries <= max_stale_recovered_retries:
                    continue
                raise

            if retry >= max_retries:
                raise

            delay = _LOCK_BACKOFF_S[retry]
            retry += 1

            age_str = "unknown"
            if lock_path.exists():
                try:
                    age_seconds = time.time() - lock_path.stat().st_mtime
                    age_str = f"{age_seconds:.1f}s"
                except OSError:
                    pass

            holder_str = find_holder(lock_path)

            logger.warning(
                "Git lock contention during {} (lock={}, age={}, holder={}); "
                "retrying in {:.1f}s (retry {}/{})",
                operation,
                lock_path,
                age_str,
                holder_str,
                delay,
                retry,
                max_retries,
            )

            sleep(delay)
