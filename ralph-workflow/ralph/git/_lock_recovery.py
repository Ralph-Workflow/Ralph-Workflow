from __future__ import annotations

import contextlib
import os
import subprocess
import sys
import threading
import time
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING, Final, Literal

if sys.platform != "win32":
    import fcntl

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from concurrent.futures import ThreadPoolExecutor
    from typing import Protocol

    class LivenessProbeFn(Protocol):
        def __call__(
            self,
            lock_path: Path,
            gitdir: Path,
        ) -> Literal["live", "dead", "unknown"]: ...


from git.exc import GitCommandError
from loguru import logger

from ralph.git._lock_liveness import (
    _HOST_INIT_MAX_START_S,
    _PROBE_MAX_WORKERS,
    _PROBE_THREAD_NAME_PREFIX,
    _PROBE_TIMEOUT_S,
    PROBE_MAX_WORKERS,
    ProbeExecutorStatus,
    _combine_verdicts,
    _get_probe_executor,
    _normalize_external_liveness,
    _not_host_pid_namespace,
    _pid1_started_at_boot,
    _probe_pool,
    _reset_probe_executor,
    _resolve_repo_identity,
    _scan_proc,
    probe_executor_status,
    probe_pool_capacity,
    reset_probe_executor,
)
from ralph.git._lock_paths import (
    _CONFIG_LOCK_PATTERN,
    _GIT_DIR_MARKERS,
    _KNOWN_GIT_LOCK_NAMES,
    _LOCK_FAILURE_MARKERS,
    _LOCK_PATH_PATTERN,
    _STRUCTURAL_LOCK_DIRS,
    _gitdir_for_lock,
    _is_inside_git_dir,
    _is_ralph_lock,
    _lock_base_dir,
    _lock_path_is_reapable,
    _parse_lock_path_from_error,
    _walk_locks,
    find_git_dirs,
    git_dirs_from_git_dir,
    git_lock_candidates,
    is_lock_failure,
    names_a_lock_file,
)
from ralph.git._lock_proc_inspect import (
    _GIT_OPTS_WITH_ARG,
    _INFRASTRUCTURE_DAEMONS,
    _READ_ONLY_GIT_SUBCOMMANDS,
    _UNPROVABLE_LOCK_CEILING_S,
    _can_rule_out_non_git_process,
    _check_git_liveness,
    _check_pid_liveness,
    _cmdline_targets_repo,
    _fd_is_lock,
    _git_subcommand,
    _is_dead_process_os_error,
    _is_git_process,
    _is_infrastructure_daemon,
    _is_unreadable_os_error,
    _list_pids,
    _non_git_touches_repo,
    _pid_has_gitdir_environ,
    _pid_holds_lock_fd,
    _pid_in_repo_cwd,
    _read_proc_status,
    _resolve_environ_gitdir,
    _rule_out_unreadable,
)
from ralph.git.errors import GitOperationError
from ralph.timeout_defaults import GIT_SUBPROCESS_TIMEOUT_SECONDS, GIT_TERMINATE_GRACE_SECONDS

_STALE_GIT_LOCK_AGE_SECONDS = 10.0
_LIVE_HOLDER_PATIENCE_S = GIT_SUBPROCESS_TIMEOUT_SECONDS + GIT_TERMINATE_GRACE_SECONDS
LIVE_HOLDER_PATIENCE_S: Final[float] = _LIVE_HOLDER_PATIENCE_S
_LIVE_HOLDER_POLL_S = 3.0
LIVE_HOLDER_POLL_S: Final[float] = _LIVE_HOLDER_POLL_S


_LOCK_BACKOFF_S: tuple[float, ...] = (0.2, 0.5, 1.0)
LOCK_BACKOFF_S: tuple[float, ...] = _LOCK_BACKOFF_S

_sleep_fn: Callable[[float], None] = time.sleep


def _probe_lsof_fuser(lock: Path, timeout: float) -> Literal["live", "dead", "unknown"]:
    try:
        result = subprocess.run(
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

    try:
        result = subprocess.run(
            ["fuser", str(lock)],
            timeout=timeout,
            capture_output=True,
            check=False,
        )
        if result.returncode == 0:
            return "live"
        if result.returncode == 1:
            return "dead"
    except (OSError, subprocess.TimeoutExpired, PermissionError):
        pass

    return "unknown"


def _lock_has_no_live_holder(
    lock_path: Path | str,
    gitdir: Path | str,
    gitdir_common: Path | str | None = None,
    *,
    probe_timeout_s: float = _PROBE_TIMEOUT_S,
    proc_root: Path | None = None,
    _proc_root: Path = Path("/proc"),
    probe_lsof_fuser_fn: Callable[[Path, float], Literal["live", "dead", "unknown"] | bool]
    | None = None,
    executor: ThreadPoolExecutor | None = None,
) -> Literal["live", "dead", "unknown"]:
    effective_proc_root = proc_root if proc_root is not None else _proc_root
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
                    logger.debug(
                        "Lock liveness probe returning 'unknown' for {}: failed to resolve "
                        "repository identity ({}: {}); refusing to unlink without proven "
                        "negative liveness",
                        lock_path,
                        type(exc).__name__,
                        exc,
                    )
            return "unknown"

        try:
            lock_stat: os.stat_result | None = lock.stat()
        except OSError:
            lock_stat = None

        proc_status = _scan_proc(
            effective_proc_root,
            lock,
            live_cwds,
            common_worktrees_dir,
            gitdir_bytes,
            gitdir_common_bytes,
            lock_stat=lock_stat,
            deadline=probe_start + probe_timeout_s,
        )
        if (
            proc_status == "dead"
            and effective_proc_root == Path("/proc")
            and _not_host_pid_namespace()
        ):
            proc_status = "unknown"
        if proc_status == "live":
            return "live"

        remaining_s = probe_start + probe_timeout_s - time.monotonic()
        external: Literal["live", "dead", "unknown"] = "unknown"
        if (
            effective_proc_root == Path("/proc") or probe_lsof_fuser_fn is not None
        ) and remaining_s > 0:
            external = _normalize_external_liveness(lsof_fuser(lock, remaining_s))
        verdict = _combine_verdicts(proc_status, external, has_proc=effective_proc_root.exists())
        if verdict == "unknown" and time.monotonic() - probe_start < probe_timeout_s:
            platform_reason = (
                "/proc scan could not rule out every process (an uninspectable "
                "process older than the lock may hold it)"
                if effective_proc_root.exists()
                else "no /proc scan succeeded"
            )
            with suppress(Exception):
                logger.debug(
                    "Lock liveness probe returning 'unknown' for {}: {}; refusing to "
                    "unlink without proven negative liveness",
                    lock_path,
                    platform_reason,
                )
        return verdict

    active_executor = executor if executor is not None else _get_probe_executor()
    future = active_executor.submit(_probe)
    try:
        return future.result(timeout=probe_timeout_s)
    except TimeoutError:
        logger.debug(
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


lock_has_no_live_holder = _lock_has_no_live_holder


def _lock_identity(lock_path: Path) -> tuple[int, int, int, bytes] | None:
    try:
        with lock_path.open("rb") as handle:
            st = os.fstat(handle.fileno())
            content = handle.read()
    except OSError:
        return None
    return st.st_dev, st.st_ino, st.st_mtime_ns, content


def _recover_stale_git_lock(
    operation: str,
    error: Exception,
    *,
    liveness_fn: LivenessProbeFn | None = None,
    now_fn: Callable[[], float] | None = None,
) -> bool:
    lock_path = _parse_lock_path_from_error(str(error))
    if lock_path is None:
        return False
    return _reap_stale_lock(operation, lock_path, liveness_fn=liveness_fn, now_fn=now_fn)


recover_stale_git_lock = _recover_stale_git_lock


def _stale_age_or_none(
    operation: str,
    lock_path: Path,
    *,
    now_fn: Callable[[], float] | None = None,
) -> float | None:
    now = now_fn() if now_fn is not None else time.time()
    age_seconds = now - lock_path.stat().st_mtime
    if age_seconds < _STALE_GIT_LOCK_AGE_SECONDS:
        logger.warning(
            "Refusing to auto-unlink git lock for {} (path={}, age={:.1f}s < {}s); "
            "too young to be proven stale.",
            operation,
            lock_path,
            age_seconds,
            _STALE_GIT_LOCK_AGE_SECONDS,
        )
        return None
    return age_seconds


_reap_thread_lock = threading.Lock()


def _flock(fd: int, *, exclusive: bool) -> None:
    if sys.platform != "win32":
        fcntl.flock(fd, fcntl.LOCK_EX if exclusive else fcntl.LOCK_UN)


@contextlib.contextmanager
def _reap_mutex(ralph_dir: Path) -> Iterator[None]:
    with _reap_thread_lock:
        ralph_dir.mkdir(parents=True, exist_ok=True)
        p = ralph_dir / "reap.mutex"
        with p.open("a+b") as handle:
            _flock(handle.fileno(), exclusive=True)
            try:
                yield
            finally:
                _flock(handle.fileno(), exclusive=False)


def _unlink_if_unchanged(
    lock_path: Path, probed_identity: tuple[int, int, int, bytes] | None
) -> bool:
    ralph_dir = _gitdir_for_lock(lock_path) / "ralph"
    with _reap_mutex(ralph_dir):
        if _lock_identity(lock_path) != probed_identity:
            return False
        aside = ralph_dir / f"reap-{os.getpid()}-{time.time_ns()}"
        lock_path.rename(aside)
        if _lock_identity(aside) == probed_identity:
            aside.unlink()
            return True
        with suppress(OSError):
            os.link(aside, lock_path)
        aside.unlink(missing_ok=True)
        return False


def _precheck_lock(operation: str, lock_path: Path) -> bool | None:
    if not _lock_path_is_reapable(lock_path):
        logger.warning("Refusing to touch {}: not a reapable git lock path.", lock_path)
        return False
    if not lock_path.exists():
        logger.warning(
            "Retrying {} after transient git lock contention; lock path already disappeared: {}",
            operation,
            lock_path,
        )
        return True
    return None


def _reap_stale_lock(
    operation: str,
    lock_path: Path,
    *,
    liveness_fn: LivenessProbeFn | None = None,
    gitdir: Path | None = None,
    gitdir_common: Path | None = None,
    now_fn: Callable[[], float] | None = None,
) -> bool:
    gone_or_refused = _precheck_lock(operation, lock_path)
    if gone_or_refused is not None:
        return gone_or_refused

    try:
        age_seconds = _stale_age_or_none(operation, lock_path, now_fn=now_fn)
        if age_seconds is None:
            return False
        probed_identity = _lock_identity(lock_path)
        resolved_gitdir = gitdir if gitdir is not None else _gitdir_for_lock(lock_path)
        if liveness_fn is not None:
            liveness = liveness_fn(lock_path, resolved_gitdir)
        else:
            liveness = _lock_has_no_live_holder(lock_path, resolved_gitdir, gitdir_common)
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
        if not _unlink_if_unchanged(lock_path, probed_identity):
            logger.warning(
                "Refusing to auto-unlink git lock for {} (path={}): the lock changed "
                "while its liveness was being probed.",
                operation,
                lock_path,
            )
            return False
        logger.warning(
            "Recovered stale git lock for {} by removing {} (liveness=dead)", operation, lock_path
        )
        return True
    except OSError:
        return False


def reap_stale_locks(
    git_dir: Path,
    common_dir: Path | None = None,
    *,
    operation: str = "repair",
) -> list[Path]:
    removed: list[Path] = []
    for lock in git_lock_candidates(git_dir, common_dir):
        if not lock.exists():
            continue
        if _reap_stale_lock(
            operation, lock, gitdir=_gitdir_for_lock(lock), gitdir_common=common_dir
        ):
            removed.append(lock)
    return removed


def reap_known_stale_locks(git_dir: Path, common_dir: Path | None, *, operation: str) -> int:
    removed = 0
    roots = [git_dir] if common_dir is None or common_dir == git_dir else [git_dir, common_dir]
    for root in roots:
        for name in _KNOWN_GIT_LOCK_NAMES:
            lock = root / name
            if lock.exists() and _reap_stale_lock(
                operation, lock, gitdir=git_dir, gitdir_common=common_dir
            ):
                removed += 1
    return removed


def retry_after_lock_failure(
    git_dir: Path,
    common_dir: Path | None,
    stderr: str,
    *,
    cwd: Path,
    operation: str,
    sleep: Callable[[float], None] | None = None,
) -> bool:
    pause = sleep if sleep is not None else _sleep_fn
    named = _parse_lock_path_from_error(stderr, base_dir=cwd)
    started = time.monotonic()
    while True:
        if reap_stale_locks(git_dir, common_dir, operation=operation):
            return True
        pending = [
            p
            for p in ([named] if named else git_lock_candidates(git_dir, common_dir))
            if p.exists()
        ]
        if not pending:
            return True
        if time.monotonic() - started >= _LIVE_HOLDER_PATIENCE_S:
            return False
        if not any(_lock_holder_is_waitable(p) for p in pending):
            return False
        pause(_LIVE_HOLDER_POLL_S)


def _lock_holder_is_waitable(lock_path: Path) -> bool:
    try:
        age_seconds = time.time() - lock_path.stat().st_mtime
    except OSError:
        return False
    if age_seconds < _STALE_GIT_LOCK_AGE_SECONDS:
        return True
    try:
        return _lock_has_no_live_holder(lock_path, _gitdir_for_lock(lock_path)) != "dead"
    except OSError:
        return False


def _find_lock_holder(lock_path: Path) -> str:
    if not lock_path.exists():
        return "unknown"
    try:
        result = subprocess.run(
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
        result = subprocess.run(
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
    recover_fn: Callable[[str, Exception], bool] | None = None,
    sleep_fn: Callable[[float], None] | None = None,
    holder_fn: Callable[[Path], str] | None = None,
    waitable_fn: Callable[[Path], bool] | None = None,
) -> T:
    retry = 0
    max_retries = len(_LOCK_BACKOFF_S)
    stale_recovered_retries = 0
    max_stale_recovered_retries = 3

    recover = recover_fn if recover_fn is not None else _recover_stale_git_lock
    sleep = sleep_fn if sleep_fn is not None else _sleep_fn
    find_holder = holder_fn if holder_fn is not None else _find_lock_holder
    waitable = waitable_fn if waitable_fn is not None else _lock_holder_is_waitable
    slept_on_holder_s = 0.0
    holder_wait_started: float | None = None

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
                if holder_wait_started is None:
                    holder_wait_started = time.monotonic()
                waited_on_live_holder_s = max(
                    slept_on_holder_s, time.monotonic() - holder_wait_started
                )
                if (
                    waited_on_live_holder_s < _LIVE_HOLDER_PATIENCE_S
                    and lock_path.exists()
                    and waitable(lock_path)
                ):
                    logger.warning(
                        "Git lock {} held by a live operation during {}; waiting "
                        "(waited {:.0f}s of {:.0f}s)",
                        lock_path,
                        operation,
                        waited_on_live_holder_s,
                        _LIVE_HOLDER_PATIENCE_S,
                    )
                    sleep(_LIVE_HOLDER_POLL_S)
                    slept_on_holder_s += _LIVE_HOLDER_POLL_S
                    continue
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


run_git_operation_with_stale_lock_recovery = _run_git_operation_with_stale_lock_recovery


__all__ = [
    "LIVE_HOLDER_PATIENCE_S",
    "LIVE_HOLDER_POLL_S",
    "LOCK_BACKOFF_S",
    "PROBE_MAX_WORKERS",
    "_CONFIG_LOCK_PATTERN",
    "_GIT_DIR_MARKERS",
    "_GIT_OPTS_WITH_ARG",
    "_HOST_INIT_MAX_START_S",
    "_INFRASTRUCTURE_DAEMONS",
    "_KNOWN_GIT_LOCK_NAMES",
    "_LOCK_BACKOFF_S",
    "_LOCK_FAILURE_MARKERS",
    "_LOCK_PATH_PATTERN",
    "_PROBE_MAX_WORKERS",
    "_PROBE_THREAD_NAME_PREFIX",
    "_PROBE_TIMEOUT_S",
    "_READ_ONLY_GIT_SUBCOMMANDS",
    "_STALE_GIT_LOCK_AGE_SECONDS",
    "_STRUCTURAL_LOCK_DIRS",
    "_UNPROVABLE_LOCK_CEILING_S",
    "ProbeExecutorStatus",
    "_can_rule_out_non_git_process",
    "_check_git_liveness",
    "_check_pid_liveness",
    "_cmdline_targets_repo",
    "_combine_verdicts",
    "_fd_is_lock",
    "_find_lock_holder",
    "_flock",
    "_get_probe_executor",
    "_git_subcommand",
    "_gitdir_for_lock",
    "_is_dead_process_os_error",
    "_is_git_process",
    "_is_infrastructure_daemon",
    "_is_inside_git_dir",
    "_is_ralph_lock",
    "_is_unreadable_os_error",
    "_list_pids",
    "_lock_base_dir",
    "_lock_has_no_live_holder",
    "_lock_holder_is_waitable",
    "_lock_identity",
    "_lock_path_is_reapable",
    "_non_git_touches_repo",
    "_normalize_external_liveness",
    "_not_host_pid_namespace",
    "_parse_lock_path_from_error",
    "_pid1_started_at_boot",
    "_pid_has_gitdir_environ",
    "_pid_holds_lock_fd",
    "_pid_in_repo_cwd",
    "_precheck_lock",
    "_probe_lsof_fuser",
    "_probe_pool",
    "_read_proc_status",
    "_reap_mutex",
    "_reap_stale_lock",
    "_reap_thread_lock",
    "_recover_stale_git_lock",
    "_reset_probe_executor",
    "_resolve_environ_gitdir",
    "_resolve_repo_identity",
    "_rule_out_unreadable",
    "_run_git_operation_with_stale_lock_recovery",
    "_scan_proc",
    "_sleep_fn",
    "_stale_age_or_none",
    "_unlink_if_unchanged",
    "_walk_locks",
    "find_git_dirs",
    "git_dirs_from_git_dir",
    "git_lock_candidates",
    "is_lock_failure",
    "lock_has_no_live_holder",
    "names_a_lock_file",
    "probe_executor_status",
    "probe_pool_capacity",
    "reap_known_stale_locks",
    "reap_stale_locks",
    "recover_stale_git_lock",
    "reset_probe_executor",
    "retry_after_lock_failure",
    "run_git_operation_with_stale_lock_recovery",
]
