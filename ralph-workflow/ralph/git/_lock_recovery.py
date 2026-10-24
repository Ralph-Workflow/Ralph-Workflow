"""Git stale lock recovery and contention handling.

Extracted from ``ralph.git.operations`` to stay within repo-structure line limits.
Provides an age-and-liveness gate to safely unlink stale Git lock files,
along with bounded backoff retry for transient contention.

The gate itself lives here: it owns the bounded external (``lsof`` / ``fuser``)
corroboration, the cross-process reaper mutex and the retry/backoff loop. The
mechanisms it drives live in the sibling modules and are re-exported below so
every existing importer keeps working:

* :mod:`ralph.git._lock_paths` -- lock path validation and git-dir discovery.
* :mod:`ralph.git._lock_proc_inspect` -- per-process ``/proc`` inspection.
* :mod:`ralph.git._lock_liveness` -- bounded probe pool and verdict aggregation.
"""

from __future__ import annotations

import contextlib
import os
import subprocess
import sys
import threading
import time
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING, Literal

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
    _combine_verdicts,
    _get_probe_executor,
    _normalize_external_liveness,
    _not_host_pid_namespace,
    _pid1_started_at_boot,
    _probe_pool,
    _reset_probe_executor,
    _resolve_repo_identity,
    _scan_proc,
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

#: Conservative age floor: a lock younger than this is never auto-unlinked.
_STALE_GIT_LOCK_AGE_SECONDS = 10.0
#: How long to keep waiting on a lock whose holder is provably alive. Every
#: git call Ralph makes is killed at ``GIT_SUBPROCESS_TIMEOUT_SECONDS`` (plus
#: the SIGTERM grace in which git removes its own locks), so a live holder
#: either finishes inside this window or releases the lock when it is killed.
#: A legitimately slow git (gc, large ``add -A``) must not burn the caller's
#: bounded recovery budget; only a lock that outlives a holder's own ceiling
#: is treated as contention to surface.
_LIVE_HOLDER_PATIENCE_S = GIT_SUBPROCESS_TIMEOUT_SECONDS + GIT_TERMINATE_GRACE_SECONDS
_LIVE_HOLDER_POLL_S = 3.0


# Bounded backoff constants for git lock contention retry
_LOCK_BACKOFF_S: tuple[float, ...] = (0.2, 0.5, 1.0)  # total ≤ 1.7s
#: Public observation seam for bounded backoff schedule.
LOCK_BACKOFF_S: tuple[float, ...] = _LOCK_BACKOFF_S

# Seam for time.sleep — patch in tests to avoid real delays
_sleep_fn: Callable[[float], None] = time.sleep


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
                    logger.warning(
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

        # /proc is the proof: an open fd on the lock is the only positive
        # evidence of a holder, and every other process is ruled out by a sound
        # fact or leaves the verdict "unknown". It runs first and is bounded by
        # the probe deadline.
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
            # A nested pid namespace shows only its own processes; "nobody else is
            # here" proves nothing about a git running on the host.
            proc_status = "unknown"
        if proc_status == "live":
            return "live"

        # lsof/fuser can only VETO. Neither can see a process whose fd table is
        # unreadable and both exit "nothing found" for it, so on a /proc
        # platform their silence is not evidence; their "live" is.
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
                logger.warning(
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


#: Public observation seam for lock liveness probe.
lock_has_no_live_holder = _lock_has_no_live_holder


def _lock_identity(lock_path: Path) -> tuple[int, int, int] | None:
    """Return ``(st_dev, st_ino, st_mtime_ns)`` or None when the lock is gone."""
    try:
        st = lock_path.stat()
    except OSError:
        return None
    return st.st_dev, st.st_ino, st.st_mtime_ns


def _recover_stale_git_lock(
    operation: str,
    error: Exception,
    *,
    liveness_fn: LivenessProbeFn | None = None,
    now_fn: Callable[[], float] | None = None,
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

    Args:
        operation: Human-readable operation name for log messages.
        error: The git error that named the lock file.
        liveness_fn: Optional override for the liveness probe.
        now_fn: Optional injected clock for the age gate. Production uses the
            wall clock; a test injects a deterministic one so the age gate can
            be exercised without mutating filesystem timestamps.

    Returns:
        True  -- lock removed or already gone (caller can retry).
        False -- lock is live, too young, or unprovable (caller
                 surfaces the contention).
    """
    lock_path = _parse_lock_path_from_error(str(error))
    if lock_path is None:
        return False
    return _reap_stale_lock(operation, lock_path, liveness_fn=liveness_fn, now_fn=now_fn)


#: Public observation seam for stale git lock recovery.
recover_stale_git_lock = _recover_stale_git_lock


def _stale_age_or_none(
    operation: str,
    lock_path: Path,
    *,
    now_fn: Callable[[], float] | None = None,
) -> float | None:
    """Return the lock's age when it is old enough to be considered, else None.

    ``now_fn`` is the injected clock; the default is the wall clock.
    """
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
    """``flock`` where the platform has it; Windows relies on the thread lock alone."""
    if sys.platform != "win32":
        fcntl.flock(fd, fcntl.LOCK_EX if exclusive else fcntl.LOCK_UN)


@contextlib.contextmanager
def _reap_mutex(ralph_dir: Path) -> Iterator[None]:
    """Serialise Ralph's own reapers (threads and processes) for one git dir.

    Two removers racing on the same stale lock is how a freshly re-created LIVE
    lock gets moved aside; one reaper at a time closes that. Foreign git
    processes are not serialised by this, which is why the identity re-check
    still follows.
    """
    with _reap_thread_lock:
        ralph_dir.mkdir(parents=True, exist_ok=True)
        p = ralph_dir / "reap.mutex"
        with p.open("a+b") as handle:  # filesystem-write-ok: flock mutex
            _flock(handle.fileno(), exclusive=True)
            try:
                yield
            finally:
                _flock(handle.fileno(), exclusive=False)


def _unlink_if_unchanged(lock_path: Path, probed_identity: tuple[int, int, int] | None) -> bool:
    """Remove ``lock_path`` only if it is still the exact file that was probed.

    The identity is re-checked immediately before the lock is renamed into
    Ralph's private directory (never into the ref namespace, where git would see
    a broken ref), verified again on the moved file, and only then unlinked. If
    it turns out to be a different file it is linked straight back.
    """
    ralph_dir = _gitdir_for_lock(lock_path) / "ralph"
    with _reap_mutex(ralph_dir):
        if _lock_identity(lock_path) != probed_identity:
            return False
        aside = ralph_dir / f"reap-{os.getpid()}-{time.time_ns()}"
        lock_path.rename(aside)
        if _lock_identity(aside) == probed_identity:
            aside.unlink()  # filesystem-write-ok: stale git lock removal after age + proven-dead + identity checks
            return True
        with suppress(OSError):
            os.link(aside, lock_path)
        aside.unlink(missing_ok=True)
        return False


def _precheck_lock(operation: str, lock_path: Path) -> bool | None:
    """Return False to refuse, True when already gone, None to continue probing."""
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
    """Remove ``lock_path`` iff it is old enough AND provably has no live holder.

    The one place a git lock is ever unlinked. Returns True when the lock is gone
    (removed here or already absent), False when it is young, held, unprovable,
    not a reapable git lock path, or changed while it was being probed.
    """
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
    """Remove every orphaned git lock under ``git_dir``/``common_dir``; return those removed.

    Each lock goes through the same age + proven-dead + unchanged-identity gate
    as the in-call recovery; held or unprovable locks are left alone.
    """
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
    """Pre-flight: remove provably-dead top-level locks (index, HEAD, config, ...) before git runs.

    Cheap -- a handful of ``exists`` checks -- and it prevents the failure
    outright, so a command that partially applies before hitting a lock
    (``checkout -b``) never has to be retried.
    """
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
    """Clear (or wait out) the lock that blocked a git command; True means "retry it".

    Orphaned locks are reaped through the usual gate. A lock whose holder is
    live or cannot be ruled out is waited on, bounded by real elapsed time:
    every git Ralph runs is killed at its own ceiling, so a live holder either
    finishes or releases the lock. The caller decides whether the command may be
    repeated: git takes the lock before the side effect of the single step it
    protects, but a multi-step command (merge, rebase, checkout -b) can have
    applied part of its work before it hit a lock.
    """
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
    """Return True when the lock is held by an operation that can still finish.

    A lock younger than the staleness floor, or one whose holder is live or
    cannot be ruled out, is somebody's in-flight work that may still finish.
    """
    try:
        age_seconds = time.time() - lock_path.stat().st_mtime
    except OSError:
        return False
    if age_seconds < _STALE_GIT_LOCK_AGE_SECONDS:
        return True
    try:
        # "unknown" (an uninspectable process older than the lock) may still be
        # a holder that finishes; only a proven "dead" is not worth waiting for
        # (and that case was already offered to recovery by the caller).
        return _lock_has_no_live_holder(lock_path, _gitdir_for_lock(lock_path)) != "dead"
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
    waitable_fn: Callable[[Path], bool] | None = None,
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
                # Real elapsed time bounds the wait: probes and the retried
                # action take time that the poll sleeps alone do not account for.
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


#: Public seam for running git operations with bounded lock backoff.
run_git_operation_with_stale_lock_recovery = _run_git_operation_with_stale_lock_recovery


# Re-exported surface of the sibling modules that own the lock mechanisms.
# Every name below was defined in this module before the split; keeping them
# importable from ``ralph.git._lock_recovery`` preserves the public contract.
__all__ = [
    "LOCK_BACKOFF_S",
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
    "reap_known_stale_locks",
    "reap_stale_locks",
    "recover_stale_git_lock",
    "retry_after_lock_failure",
    "run_git_operation_with_stale_lock_recovery",
]
