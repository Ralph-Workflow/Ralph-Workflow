"""Stale git lock recovery and contention retry.

Provides bounded lock recovery via age and negative-liveness checks,
as well as bounded exponential backoff for operations experiencing
lock contention.
"""

from __future__ import annotations

import os
import re
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from collections.abc import Callable

from git import GitCommandError
from loguru import logger

from ralph.git.errors import GitOperationError
from ralph.git.subprocess_runner import run_git

_LOCK_PATH_PATTERN = re.compile(r"Unable to create '([^']+\.lock)'")
_RECOVERABLE_GIT_LOCK_FILES = frozenset({"index.lock", "HEAD.lock", "packed-refs.lock"})
_STALE_GIT_LOCK_AGE_SECONDS = 10.0
_PROBE_TIMEOUT_S = 0.5  # whole-probe ThreadPoolExecutor and subprocess budget

type _LivenessVerdict = Literal["live", "dead", "unknown"]
type _LivenessProbeFn = Callable[[Path | str, Path | str], _LivenessVerdict]

# Bounded backoff constants for git lock contention retry
_LOCK_BACKOFF_S: tuple[float, ...] = (0.2, 0.5, 1.0)  # total ≤ 1.7s
_MAX_LOCK_RECOVERY_ATTEMPTS: int = 3

# Seam for time.sleep — patch in tests to avoid real delays
_sleep_fn: Callable[[float], None] = time.sleep


def _parse_lock_path_from_error(error_text: str) -> Path | None:
    match = _LOCK_PATH_PATTERN.search(error_text)
    if match is None:
        return None
    lock_path = Path(match.group(1))
    if lock_path.name not in _RECOVERABLE_GIT_LOCK_FILES:
        return None
    return lock_path


def _is_pid_alive(pid: int) -> bool:
    """Return True iff ``pid`` resolves to a running process.

    Cross-platform best-effort: ``os.kill(pid, 0)`` returns 0 if the
    process is alive and raises ``ProcessLookupError``/``PermissionError``
    otherwise. ``PermissionError`` means the PID exists but is owned by
    another user (we treat that as "alive" so we never unlink a live
    lock by mistake).
    """
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _resolve_gitdirs(repo_root: Path) -> tuple[Path, Path]:
    """Resolve per-worktree gitdir and common gitdir via git rev-parse."""
    try:
        result = run_git(
            ("rev-parse", "--git-dir"),
            cwd=repo_root,
            label="recovery-git-dir",
        )
        gitdir_raw = result.stdout.strip() if result.returncode == 0 else None
    except OSError:
        gitdir_raw = None

    try:
        result = run_git(
            ("rev-parse", "--git-common-dir"),
            cwd=repo_root,
            label="recovery-git-common-dir",
        )
        gitdir_common_raw = result.stdout.strip() if result.returncode == 0 else None
    except OSError:
        gitdir_common_raw = None

    gitdir = Path(gitdir_raw).resolve() if gitdir_raw else (repo_root / ".git").resolve()
    gitdir_common = Path(gitdir_common_raw).resolve() if gitdir_common_raw else gitdir.parent

    return gitdir, gitdir_common


def _probe_lsof_fuser(lock: Path, timeout: float) -> bool:
    # mcp-timeout-ok: bounded lock-liveness probe
    try:
        result = subprocess.run(  # resource-lifecycle-ok: bounded read-only lock probe  # filesystem-poll-ok: read-only lsof probe for git lock liveness, not managed process
            ["lsof", "-F", "pL", str(lock)],
            timeout=timeout,
            capture_output=True,
            check=False,
        )
        if result.returncode == 0 and b"p" in result.stdout:
            return True
    except (OSError, subprocess.TimeoutExpired, PermissionError):
        pass

    # mcp-timeout-ok: bounded lock-liveness probe
    try:
        result = subprocess.run(  # resource-lifecycle-ok: bounded read-only lock probe  # filesystem-poll-ok: read-only fuser probe for git lock liveness, not managed process
            ["fuser", str(lock)],
            timeout=timeout,
            capture_output=True,
            check=False,
        )
        if result.returncode == 0:
            return True
    except (OSError, subprocess.TimeoutExpired, PermissionError):
        pass

    return False


def _check_pid_liveness(
    proc_root: Path,
    pid_str: str,
    lock_resolved: str,
    live_cwds: set[Path],
    common_worktrees_dir: Path,
    gitdir_bytes: bytes,
    gitdir_common_bytes: bytes,
) -> tuple[bool, bool]:
    """Check liveness signals for a single process PID.

    Returns (is_live, is_verified).
    """
    try:
        # (i) fd check
        fd_dir = proc_root / pid_str / "fd"
        if fd_dir.is_dir():
            for fd_link in fd_dir.iterdir():
                try:
                    if str(fd_link.readlink()) == lock_resolved:
                        return True, True
                except OSError:
                    continue

        # (ii) cwd check
        cwd_link = proc_root / pid_str / "cwd"
        if cwd_link.is_symlink() or cwd_link.exists():
            cwd_target = cwd_link.readlink()
            try:
                cwd_resolved = cwd_target.resolve()
            except OSError:
                cwd_resolved = cwd_target
            if (
                bool({cwd_target, cwd_resolved} & (live_cwds | {common_worktrees_dir}))
                or str(cwd_target).startswith(str(common_worktrees_dir) + "/")
                or str(cwd_resolved).startswith(str(common_worktrees_dir) + "/")
            ):
                return True, True

        # (iii) environ GIT_DIR check
        environ_path = proc_root / pid_str / "environ"
        if environ_path.is_file():
            environ_bytes = environ_path.read_bytes()
            if (
                b"GIT_DIR=" + gitdir_bytes in environ_bytes
                or b"GIT_DIR=" + gitdir_common_bytes in environ_bytes
            ):
                return True, True

        return False, True
    except (PermissionError, FileNotFoundError, OSError):
        return False, False


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

    my_pid = os.getpid()
    if my_pid in pids:
        pids_to_scan = [my_pid, *[p for p in pids if p != my_pid][:119]]
    else:
        pids_to_scan = pids[:120]

    # Truncated scan: there are more PIDs than we will examine. We cannot
    # prove the lock has no holder beyond the scan window, so refuse to
    # return "dead" -- the helper must fail closed.
    scan_truncated = len(pids) > len(pids_to_scan)

    lock_resolved = str(lock.resolve())
    any_verified = False
    for pid in pids_to_scan:
        is_live, is_verified = _check_pid_liveness(
            proc_root,
            str(pid),
            lock_resolved,
            live_cwds,
            common_worktrees_dir,
            gitdir_bytes,
            gitdir_common_bytes,
        )
        if is_live:
            return "live"
        if is_verified:
            any_verified = True

    if scan_truncated or not any_verified:
        return "unknown"
    return "dead"


def _lock_has_no_live_holder(
    lock_path: Path | str,
    gitdir: Path | str,
    gitdir_common: Path | str | None = None,
    *,
    probe_timeout_s: float = _PROBE_TIMEOUT_S,
    _proc_root: Path = Path("/proc"),
) -> Literal["live", "dead", "unknown"]:
    """Return whether the lock has no live holder, or "unknown" if unprovable.

    Liveness signals are OR'd: any single check returning "live" is
    sufficient to refuse lock removal (negative liveness proof).
    The whole probe runs inside a ``ThreadPoolExecutor`` with a bounded
    ``probe_timeout_s`` (0.5 s) budget; on ``TimeoutError`` or error,
    the helper returns "unknown" (fail-closed).
    """
    lock = Path(lock_path)
    gitdir_path = Path(gitdir).resolve()
    if gitdir_common is not None:
        gitdir_common_path = Path(gitdir_common).resolve()
    else:
        commondir_file = gitdir_path / "commondir"
        if commondir_file.is_file():
            try:
                rel = commondir_file.read_text().strip()
                gitdir_common_path = (gitdir_path / rel).resolve()
            except OSError:
                gitdir_common_path = gitdir_path
        elif gitdir_path.parent.name == "worktrees":
            gitdir_common_path = gitdir_path.parent.parent.resolve()
        else:
            gitdir_common_path = gitdir_path

    worktree_top: Path | None = None
    gitdir_file = gitdir_path / "gitdir"
    if gitdir_file.is_file():
        try:
            wt_dot_git = Path(gitdir_file.read_text().strip()).resolve()
            worktree_top = wt_dot_git.parent
        except OSError:
            pass
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

    def _probe() -> Literal["live", "dead", "unknown"]:
        if os.access(_proc_root, os.R_OK):
            proc_verdict = _scan_proc(
                _proc_root,
                lock,
                live_cwds,
                common_worktrees_dir,
                gitdir_bytes,
                gitdir_common_bytes,
            )
            if proc_verdict in ("live", "dead"):
                return proc_verdict
        if _probe_lsof_fuser(lock, probe_timeout_s):
            return "live"
        return "unknown"

    executor = ThreadPoolExecutor(max_workers=1)
    try:
        future = executor.submit(_probe)
        return future.result(timeout=probe_timeout_s)
    except Exception:
        # TimeoutError or any other thread error: fail closed.
        # ``shutdown(wait=False)`` so a permanently stalled probe cannot
        # block this caller past the deadline -- ``with ThreadPoolExecutor``
        # would wait for the worker thread on context exit and re-introduce
        # the unbounded block.
        return "unknown"
    finally:
        executor.shutdown(wait=False)


def _recover_stale_git_lock(
    operation: str,
    error: Exception,
    *,
    liveness_probe: _LivenessProbeFn | None = None,
) -> bool:
    """Recover from a transient Git lock-file error using an age-and-liveness gate."""
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

        gitdir = lock_path.parent
        probe_fn = liveness_probe or _lock_has_no_live_holder
        liveness = probe_fn(lock_path, gitdir)
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
        result = subprocess.run(  # resource-lifecycle-ok: bounded read-only lock holder discovery  # filesystem-poll-ok: read-only lsof probe for lock holder PID discovery
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
        result = subprocess.run(  # resource-lifecycle-ok: bounded read-only lock holder discovery  # filesystem-poll-ok: read-only fuser probe for lock holder PID discovery
            ["fuser", str(lock_path)],
            timeout=_PROBE_TIMEOUT_S,
            capture_output=True,
            text=True,
            check=False,
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
    sleep_fn: Callable[[float], None] | None = None,
    recover_fn: Callable[[str, Exception], bool] | None = None,
) -> T:
    """Run a git action with bounded backoff on persistent lock contention."""
    retry = 0
    max_retries = len(_LOCK_BACKOFF_S)
    stale_recovered_retries = 0
    max_stale_recovered_retries = 3
    sleep = sleep_fn or _sleep_fn
    recover = recover_fn or _recover_stale_git_lock

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

            holder_str = _find_lock_holder(lock_path)

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
