"""Git stale lock recovery and contention handling.

Extracted from ``ralph.git.operations`` to stay within repo-structure line limits.
Provides an age-and-liveness gate to safely unlink stale Git lock files,
along with bounded backoff retry for transient contention.

The READ-ONLY process inspection that backs the liveness verdict lives in
``ralph.git._lock_liveness``; the bounded lsof/fuser probe, the age gate, the
identity-checked unlink and the retry policy live here.
"""

from __future__ import annotations

import atexit
import contextlib
import os
import re
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from contextvars import ContextVar
from pathlib import Path
from typing import TYPE_CHECKING, Literal

if sys.platform != "win32":
    import fcntl

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
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
    _combine_verdicts,
    _normalize_external_liveness,
    _not_host_pid_namespace,
    _resolve_repo_identity,
    _scan_proc,
)
from ralph.git.errors import GitOperationError
from ralph.timeout_defaults import GIT_SUBPROCESS_TIMEOUT_SECONDS, GIT_TERMINATE_GRACE_SECONDS

_LOCK_PATH_PATTERN = re.compile(r"Unable to create '(.+?\.lock)'", re.IGNORECASE)
_STALE_GIT_LOCK_AGE_SECONDS = 10.0
#: Wall-clock bound on one liveness probe. The scan itself is cheap (a few
#: reads per process); the bound exists for a process table that is huge or a
#: /proc entry that hangs, and is generous because the probe only runs after
#: a lock has already blocked git for at least the staleness floor.
_PROBE_TIMEOUT_S = 5.0
_PROBE_MAX_WORKERS: int = 4
#: How long to keep waiting on a lock whose holder is provably alive. Every
#: git call Ralph makes is killed at ``GIT_SUBPROCESS_TIMEOUT_SECONDS`` (plus
#: the SIGTERM grace in which git removes its own locks), so a live holder
#: either finishes inside this window or releases the lock when it is killed.
#: A legitimately slow git (gc, large ``add -A``) must not burn the caller's
#: bounded recovery budget; only a lock that outlives a holder's own ceiling
#: is treated as contention to surface.
_LIVE_HOLDER_PATIENCE_S = GIT_SUBPROCESS_TIMEOUT_SECONDS + GIT_TERMINATE_GRACE_SECONDS
_LIVE_HOLDER_POLL_S = 3.0
#: A lock that no inspectable process holds, but that an uninspectable process
#: could still hold, is reaped once it is this old. No git operation holds
#: ``index.lock`` or a ref lock for an hour; the pipeline waits and retries
#: until then, so an orphan is always eventually recoverable while a lock that
#: might be live is never removed early.
_UNPROVABLE_LOCK_CEILING_S = 3600.0


#: Thread-name prefix of the bounded probe pool. It is a named constant so a
#: test can observe the pool's worker count through ``threading.enumerate()``
#: instead of reaching into a private attribute of the executor object.
_PROBE_THREAD_NAME_PREFIX = "ralph-git-lock-probe"


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


#: Lock names git itself uses at the top of a git dir. They are accepted by name
#: (the path in an error message may be relative or reached through a symlink);
#: any other ``*.lock`` must be located inside a recognisable git dir.
_KNOWN_GIT_LOCK_NAMES = frozenset(
    {
        "index.lock",
        "HEAD.lock",
        "packed-refs.lock",
        "config.lock",
        "shallow.lock",
        "ORIG_HEAD.lock",
        "MERGE_HEAD.lock",
        "FETCH_HEAD.lock",
    }
)
_CONFIG_LOCK_PATTERN = re.compile(r"could not lock config file (.+?): File exists", re.IGNORECASE)
_GIT_DIR_MARKERS = ("refs", "logs", "objects", "modules")


#: Directories under a git dir whose ``*.lock`` files are git's (ref, reflog,
#: submodule and linked-worktree locks).
_STRUCTURAL_LOCK_DIRS = frozenset({"refs", "logs", "modules", "worktrees"})
_lock_base_dir: ContextVar[Path | None] = ContextVar("ralph_git_lock_base_dir", default=None)


def _is_inside_git_dir(path: Path) -> bool:
    """Return True when ``path`` lives in a git directory (checkout, worktree or bare).

    A lock is only ever removed from a real git dir, never from an arbitrary
    path that happens to appear in an error message.
    """
    if ".git" in path.parts:
        return True
    for ancestor in list(path.parents)[:6]:
        if (ancestor / "HEAD").is_file() and (
            (ancestor / "objects").is_dir() or (ancestor / "commondir").is_file()
        ):
            return True
    return False


def _is_ralph_lock(path: Path) -> bool:
    """Ralph's own lock files (lease, auto-integrate) are never git locks."""
    return (
        path.name.startswith("ralph")
        or path.name == "agent-lease.lock"
        or path.parent.name == "ralph"
    )


def _lock_path_is_reapable(lock_path: Path) -> bool:
    """Gate every unlink: the path must be a real git lock inside a real git dir.

    The path can originate from git's stderr, which can carry repository- or
    hook-controlled text, so it is never trusted on its own: no relative paths,
    no ``..``, no symlinks (the file or any parent), no Ralph-owned locks, and
    only git's own lock names or ``*.lock`` in git's ref / reflog / submodule /
    worktree directories.
    """
    well_formed = (
        lock_path.is_absolute()
        and ".." not in lock_path.parts
        and lock_path.name.endswith(".lock")
        and not _is_ralph_lock(lock_path)
    )
    if not well_formed:
        return False
    try:
        if lock_path.is_symlink():
            return False
        real = Path(os.path.realpath(lock_path.parent)) / lock_path.name
    except OSError:
        return False
    # Every name -- git's own top-level ones included -- must sit in a real git
    # dir: the path comes from stderr, and a file called ``index.lock`` somewhere
    # else on disk is not ours to remove.
    if not _is_inside_git_dir(real):
        return False
    if lock_path.name in _KNOWN_GIT_LOCK_NAMES:
        return True
    return bool(_STRUCTURAL_LOCK_DIRS & set(real.parts))


def _parse_lock_path_from_error(error_text: str, base_dir: Path | None = None) -> Path | None:
    match = _LOCK_PATH_PATTERN.search(error_text)
    if match is not None:
        candidate = Path(match.group(1))
    else:
        config_match = _CONFIG_LOCK_PATTERN.search(error_text)
        if config_match is None:
            return None
        config_path: str = config_match[1]
        candidate = Path(config_path.strip().strip("'\"") + ".lock")
    if not candidate.is_absolute():
        base = base_dir if base_dir is not None else _lock_base_dir.get()
        if base is None:
            return None
        candidate = base / candidate
    return candidate if _lock_path_is_reapable(candidate) else None


def _gitdir_for_lock(lock_path: Path) -> Path:
    """Return the git dir (private for a linked worktree) that owns ``lock_path``."""
    parts = lock_path.parts
    if ".git" in parts:
        dot_git = len(parts) - 1 - parts[::-1].index(".git")
        # ``.git/worktrees/<name>/...`` is that worktree's private git dir.
        if parts[dot_git + 1 : dot_git + 2] == ("worktrees",) and len(parts) > dot_git + 3:
            return Path(*parts[: dot_git + 3])
        return Path(*parts[: dot_git + 1])
    marker_indexes = [parts.index(m) for m in _GIT_DIR_MARKERS if m in parts]
    if not marker_indexes:
        return lock_path.parent
    return Path(*parts[: min(marker_indexes)])


def names_a_lock_file(error_text: str) -> bool:
    """Return True when git's message names a specific lock file that blocked it.

    Stricter than :func:`is_lock_failure`: "cannot lock ref" alone also reports a
    permanent ref-name conflict, which no amount of waiting resolves.
    """
    return bool(_LOCK_PATH_PATTERN.search(error_text) or _CONFIG_LOCK_PATTERN.search(error_text))


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
            _proc_root,
            lock,
            live_cwds,
            common_worktrees_dir,
            gitdir_bytes,
            gitdir_common_bytes,
            lock_stat=lock_stat,
            deadline=probe_start + probe_timeout_s,
        )
        if proc_status == "dead" and _proc_root == Path("/proc") and _not_host_pid_namespace():
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
        if (_proc_root == Path("/proc") or probe_lsof_fuser_fn is not None) and remaining_s > 0:
            external = _normalize_external_liveness(lsof_fuser(lock, remaining_s))
        verdict = _combine_verdicts(proc_status, external, has_proc=_proc_root.exists())
        if verdict == "unknown" and time.monotonic() - probe_start < probe_timeout_s:
            platform_reason = (
                "/proc scan could not rule out every process (an uninspectable "
                "process older than the lock may hold it)"
                if _proc_root.exists()
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
        with (ralph_dir / "reap.mutex").open("a+b") as handle:
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


def _walk_locks(base: Path) -> list[Path]:
    """Return ``*.lock`` files under ``base`` without ever following a symlink."""
    found: list[Path] = []
    if base.is_symlink() or not base.is_dir():
        return found
    for dirpath, dirnames, filenames in os.walk(base, followlinks=False, onerror=lambda _e: None):
        here = Path(dirpath)
        dirnames[:] = [d for d in dirnames if not (here / d).is_symlink()]
        found.extend(here / f for f in filenames if f.endswith(".lock"))
    return found


def git_lock_candidates(git_dir: Path, common_dir: Path | None = None) -> list[Path]:
    """Return every lock file git itself could have orphaned for this checkout.

    Covers the private (per-worktree) git dir and the common dir: git's named
    top-level locks, ref and reflog locks, linked-worktree and submodule git
    dirs. Never follows a symlink, and Ralph's own lock files are never
    candidates.
    """
    roots = [git_dir] if common_dir is None or common_dir == git_dir else [git_dir, common_dir]
    found: list[Path] = []
    for root in roots:
        if root.is_symlink() or not root.is_dir():
            continue
        found.extend(
            root / name for name in sorted(_KNOWN_GIT_LOCK_NAMES) if (root / name).exists()
        )
        for sub in ("refs", "logs", "modules", "worktrees"):
            found.extend(_walk_locks(root / sub))
    seen: set[Path] = set()
    candidates: list[Path] = []
    for path in found:
        if path not in seen and _lock_path_is_reapable(path):
            seen.add(path)
            candidates.append(path)
    return candidates


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


#: Substrings in git's stderr that mean "a lock file blocked me". Some messages
#: name no lock path at all ("Unable to write index"), so recovery then looks
#: at the repository's lock files instead of parsing one out.
_LOCK_FAILURE_MARKERS = (
    ".lock': file exists",
    "cannot lock ref",
    "could not lock config file",
    "unable to write index",
    "could not write index",
    "unable to lock",
    "could not lock",
)


def git_dirs_from_git_dir(git_dir: Path) -> tuple[Path, Path | None] | None:
    """Return ``(git_dir, common_dir)`` for a path that IS a git dir, else None.

    A GitPython ``Repo.git_dir`` already names the (private) git dir, so no
    walk is needed; a duck-typed stand-in that merely carries a repo root does
    not look like one (no ``HEAD``) and is left alone.
    """
    if not (git_dir / "HEAD").is_file():
        return None
    commondir = git_dir / "commondir"
    common: Path | None = None
    if commondir.is_file():
        with suppress(OSError):
            common = (git_dir / commondir.read_text(encoding="utf-8").strip()).resolve()
    return git_dir, common


def find_git_dirs(cwd: Path) -> tuple[Path, Path | None] | None:
    """Return ``(git_dir, common_dir)`` for the checkout containing ``cwd`` using only the filesystem.

    Handles a normal checkout (``.git`` directory) and a linked worktree
    (``.git`` file pointing at ``<common>/worktrees/<name>``). No git process is
    spawned, so it is cheap enough to run before every git command.
    """
    for directory in (cwd, *cwd.parents):
        dot_git = directory / ".git"
        if dot_git.is_dir():
            return dot_git, None
        if dot_git.is_file():
            try:
                text = dot_git.read_text(encoding="utf-8").strip()
            except OSError:
                return None
            if not text.startswith("gitdir:"):
                return None
            private = Path(text.split(":", 1)[1].strip())
            if not private.is_absolute():
                private = (directory / private).resolve()
            commondir = private / "commondir"
            common: Path | None = None
            if commondir.is_file():
                with suppress(OSError):
                    common = (private / commondir.read_text(encoding="utf-8").strip()).resolve()
            return private, common
    return None


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


def is_lock_failure(stderr: str) -> bool:
    """Return True when git's stderr says a lock file blocked the command."""
    lowered = stderr.lower()
    return any(marker in lowered for marker in _LOCK_FAILURE_MARKERS)


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
