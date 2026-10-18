"""Git stale lock recovery and contention handling.

Extracted from ``ralph.git.operations`` to stay within repo-structure line limits.
Provides an age-and-liveness gate to safely unlink stale Git lock files,
along with bounded backoff retry for transient contention.
"""

from __future__ import annotations

import errno
import os
import re
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
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
from ralph.git.subprocess_runner import run_git

_LOCK_PATH_PATTERN = re.compile(r"Unable to create '([^']+\.lock)'")
_RECOVERABLE_GIT_LOCK_FILES = frozenset({"index.lock", "HEAD.lock", "packed-refs.lock"})
_STALE_GIT_LOCK_AGE_SECONDS = 10.0
_PROBE_TIMEOUT_S = 0.5  # whole-probe ThreadPoolExecutor and subprocess budget

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
        result = subprocess.run(  # resource-lifecycle-ok: read-only lsof probe with bounded timeout
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
        result = (
            subprocess.run(  # resource-lifecycle-ok: read-only fuser probe with bounded timeout
                ["fuser", str(lock)],
                timeout=timeout,
                capture_output=True,
                check=False,
            )
        )
        if result.returncode == 0:
            return True
    except (OSError, subprocess.TimeoutExpired, PermissionError):
        pass

    return False


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
    """Check if process cwd is in the workspace repository or gitdirs."""
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
        ):
            return "in_repo"
        return "not_in_repo"
    except OSError as e:
        if _is_unreadable_os_error(e):
            return "unknown"
        if _is_dead_process_os_error(e):
            return "not_in_repo"
        return "unknown"


def _pid_has_gitdir_environ(
    pid_dir: Path,
    gitdir_bytes: bytes,
    gitdir_common_bytes: bytes,
) -> Literal["matches", "no_match", "unknown"]:
    """Check if process environ sets GIT_DIR matching the repository gitdir."""
    try:
        environ_path = pid_dir / "environ"
        if not environ_path.is_file():
            return "no_match"
        environ_bytes = environ_path.read_bytes()
        if (
            b"GIT_DIR=" + gitdir_bytes in environ_bytes
            or b"GIT_DIR=" + gitdir_common_bytes in environ_bytes
        ):
            return "matches"
        return "no_match"
    except OSError as e:
        if _is_unreadable_os_error(e):
            return "unknown"
        if _is_dead_process_os_error(e):
            return "no_match"
        return "unknown"


def _check_pid_liveness(
    proc_root: Path,
    pid_str: str,
    lock_resolved: str,
    live_cwds: set[Path],
    common_worktrees_dir: Path,
    gitdir_bytes: bytes,
    gitdir_common_bytes: bytes,
) -> Literal["live", "dead", "unknown"]:
    """Check liveness signals for a single process PID.

    Returns:
        "live" -- process holds the lock file open (any process) or is a git
                  process with cwd / environ in the repository/gitdir.
        "dead" -- process does not hold the lock and is not a git process in repo.
        "unknown" -- process cannot be inspected (PermissionError etc.).
    """
    pid_dir = proc_root / pid_str

    is_non_git = _can_rule_out_non_git_process(proc_root, pid_str)

    # (i) fd check - open-fd protection applies to ALL processes
    fd_status = _pid_holds_lock_fd(pid_dir, lock_resolved)
    if is_non_git:
        # Confirmed non-git process (e.g. system daemon): return "live" if it
        # holds the lock fd; otherwise "dead" (cwd/environ do not apply).
        return "live" if fd_status == "live" else "dead"

    if fd_status != "not_live":
        return fd_status

    # (ii) cwd check - applies only to git processes
    cwd_status = _pid_in_repo_cwd(pid_dir, live_cwds, common_worktrees_dir)
    # (iii) environ GIT_DIR check - applies only to git processes
    env_status = _pid_has_gitdir_environ(pid_dir, gitdir_bytes, gitdir_common_bytes)

    if cwd_status == "unknown" or env_status == "unknown":
        return "unknown"

    # If in repo cwd or matching environ, verify whether process is git
    if cwd_status == "in_repo" or env_status == "matches":
        try:
            if _is_git_process(proc_root, pid_str):
                return "live"
        except PermissionError:
            return "unknown"

    return "dead"


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

    lock_resolved = str(lock.resolve())
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

    if has_unknown:
        return "unknown"
    return "dead"


def _lock_has_no_live_holder(
    lock_path: Path | str,
    gitdir: Path | str,
    gitdir_common: Path | str | None = None,
    *,
    probe_timeout_s: float = _PROBE_TIMEOUT_S,
    _proc_root: Path = Path("/proc"),
    probe_lsof_fuser_fn: Callable[[Path, float], bool] | None = None,
) -> Literal["live", "dead", "unknown"]:
    """Return whether the lock has no live holder, or "unknown" if unprovable.

    Liveness signals are OR'd: any single check returning "live" is
    sufficient to refuse lock removal (negative liveness proof).
    The whole probe runs inside a ``ThreadPoolExecutor`` with a bounded
    ``probe_timeout_s`` (0.5 s) budget; on ``TimeoutError`` or error,
    the helper returns "unknown" (fail-closed).

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

    lsof_fuser = probe_lsof_fuser_fn if probe_lsof_fuser_fn is not None else _probe_lsof_fuser

    def _probe() -> Literal["live", "dead", "unknown"]:
        proc_status = _scan_proc(
            _proc_root,
            lock,
            live_cwds,
            common_worktrees_dir,
            gitdir_bytes,
            gitdir_common_bytes,
        )
        if proc_status == "live":
            return "live"
        if proc_status == "dead":
            return "dead"
        # If /proc is unavailable (e.g. non-Linux platforms) or inconclusive,
        # fall back to lsof / fuser.
        if lsof_fuser(lock, probe_timeout_s):
            return "live"
        return "unknown"

    executor = ThreadPoolExecutor(max_workers=1)
    try:
        future = executor.submit(_probe)
        return future.result(timeout=probe_timeout_s)
    except Exception:
        # TimeoutError or any other thread error: fail closed
        return "unknown"
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


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

        gitdir = lock_path.parent
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
