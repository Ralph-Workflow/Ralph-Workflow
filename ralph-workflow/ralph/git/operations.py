"""Git operations for ralph pipeline via GitPython.

This module provides a high-level interface for git operations,
wrapping GitPython to provide the functionality needed by the pipeline.

Stale Git Lock Recovery:
Operations that mutate git state run through
``_run_git_operation_with_stale_lock_recovery``. When encountering git lock
contention (such as ``index.lock``, ``HEAD.lock``, or ``packed-refs.lock``),
recovery uses an age-and-liveness gate:
  - Conservative 10s age threshold (``_STALE_GIT_LOCK_AGE_SECONDS``):
    Git lock files do NOT record the PID of the holding process (correcting
    a false claim that Git writes holding PIDs into lock files). Because lock
    files cannot identify their holder by content, locks younger than 10s
    cannot be safely unlinked and are refused removal.
  - Negative liveness proof via OR predicate: verified across multiple
    independent process signals (proc fd table, proc cwd in worktree/gitdirs,
    proc environ GIT_DIR, lsof, and fuser). If any signal indicates a live
    process or liveness cannot be verified ("unknown"), removal is refused
    fail-closed.
  - Bounded 0.5s ThreadPoolExecutor probe: liveness checks run inside a worker
    thread with a strict 0.5s timeout (``_PROBE_TIMEOUT_S``) to prevent
    unbounded hangs.
  - Bounded backoff: when a lock cannot be unlinked due to contention, retries
    with bounded backoff (0.2s, 0.5s, 1.0s; total ≤ 1.7s) before re-raising.
  - Reducer integration: persistent failures trigger in-place reducer retry
    when policy defines no recovery phase, preserving the validated commit message.
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING, Literal, cast

if TYPE_CHECKING:
    from collections.abc import Callable

from git import Actor, InvalidGitRepositoryError, Repo
from git.exc import GitCommandError
from loguru import logger

from ralph.git.commit_result import CommitCreationResult
from ralph.git.errors import GitOperationError
from ralph.git.hardening import COMMIT_PIN_CONFIG_ARGS
from ralph.git.subprocess_runner import GitRunOptions, run_git

_LOCK_PATH_PATTERN = re.compile(r"Unable to create '([^']+\.lock)'")
_RECOVERABLE_GIT_LOCK_FILES = frozenset({"index.lock", "HEAD.lock", "packed-refs.lock"})
_STALE_GIT_LOCK_AGE_SECONDS = 10.0
_PORCELAIN_STATUS_PREFIX_LEN = 3
_RALPH_WORKFLOW_COAUTHOR_TRAILER = "Co-authored-by: Ralph Workflow <noreply@ralphworkflow.com>"
_PROBE_TIMEOUT_S = 0.5  # whole-probe ThreadPoolExecutor and subprocess budget

# Bounded backoff constants for git lock contention retry
_LOCK_BACKOFF_S: tuple[float, ...] = (0.2, 0.5, 1.0)  # total ≤ 1.7s
_MAX_LOCK_RECOVERY_ATTEMPTS: int = 3

# Seam for time.sleep — patch in tests to avoid real delays
_sleep_fn: Callable[[float], None] = time.sleep


def _close_repo(repo: Repo | None) -> None:
    close = cast("Callable[[], object] | None", getattr(repo, "close", None))
    if callable(close):
        close()


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
    gitdir_common = (
        Path(gitdir_common_raw).resolve() if gitdir_common_raw else gitdir.parent
    )

    return gitdir, gitdir_common


def _probe_lsof_fuser(lock: Path, timeout: float) -> bool:
    # mcp-timeout-ok: bounded lock-liveness probe
    try:
        result = subprocess.run(
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
        result = subprocess.run(
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

    pids: list[int] = [
        int(entry.name) for entry in proc_entries if entry.name.isdigit()
    ]
    if not pids:
        return "unknown"

    my_pid = os.getpid()
    if my_pid in pids:
        pids_to_scan = [my_pid, *[p for p in pids if p != my_pid][:119]]
    else:
        pids_to_scan = pids[:120]

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

    return "dead" if any_verified else "unknown"


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

    def _probe() -> Literal["live", "dead", "unknown"]:
        if _probe_lsof_fuser(lock, probe_timeout_s):
            return "live"
        return _scan_proc(
            _proc_root,
            lock,
            live_cwds,
            common_worktrees_dir,
            gitdir_bytes,
            gitdir_common_bytes,
        )

    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(_probe)
            return future.result(timeout=probe_timeout_s)
    except Exception:
        # TimeoutError or any other thread error: fail closed
        return "unknown"


def _recover_stale_git_lock(
    operation: str,
    error: Exception,
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
        liveness = _lock_has_no_live_holder(lock_path, gitdir)
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
        # mcp-timeout-ok: bounded lock holder discovery
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

    while True:
        try:
            return action()
        except (GitCommandError, GitOperationError) as exc:
            lock_path = _parse_lock_path_from_error(str(exc))
            if lock_path is None:
                raise

            if _recover_stale_git_lock(operation, exc):
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

            _sleep_fn(delay)


def _append_ralph_workflow_coauthor_trailer(message: str) -> str:
    stripped_message = message.rstrip()
    if not stripped_message:
        return message
    lowered_lines = {line.strip().lower() for line in stripped_message.splitlines()}
    if _RALPH_WORKFLOW_COAUTHOR_TRAILER.lower() in lowered_lines:
        return stripped_message
    return f"{stripped_message}\n\n{_RALPH_WORKFLOW_COAUTHOR_TRAILER}"


def _resolve_commit_identity(
    repo: Repo, author_name: str | None, author_email: str | None
) -> tuple[str, str]:
    if author_name and author_email:
        return author_name, author_email
    try:
        config = repo.config_reader()
        return (
            author_name or str(config.get_value("user", "name", "Ralph")),
            author_email or str(config.get_value("user", "email", "ralph@ai")),
        )
    except Exception:
        return author_name or "Ralph", author_email or "ralph@ai"


def _run_commit_hooks(
    repo_root: Path,
    hooks_dir: Path,
    message_path: Path,
    hook_env: dict[str, str],
) -> None:
    hook_specs: tuple[tuple[str, tuple[str, ...]], ...] = (
        ("pre-commit", ()),
        ("prepare-commit-msg", (str(message_path), "message")),
        ("commit-msg", (str(message_path),)),
    )
    for hook_name, hook_args in hook_specs:
        if not (hooks_dir / hook_name).exists():
            continue
        hook_command: tuple[str, ...] = ("hook", "run", hook_name)
        if hook_args:
            hook_command += ("--", *hook_args)
        hook_result = run_git(
            hook_command,
            cwd=repo_root,
            label=f"git-hook-{hook_name}",
            options=GitRunOptions(env=hook_env),
        )
        if hook_result.returncode != 0:
            raise GitOperationError("create_commit", hook_result.stderr.strip())


def find_repo_root(start: Path | str = Path()) -> Path:
    """Locate git repo root from start path.

    Args:
        start: Starting path for the search.

    Returns:
        Path to the repository root.

    Raises:
        GitOperationError: If not inside a git repository.
    """
    repo: Repo | None = None
    try:
        repo = Repo(start, search_parent_directories=True)
        if repo.working_tree_dir:
            return Path(repo.working_tree_dir).resolve()
        return Path(repo.working_dir).resolve()
    except InvalidGitRepositoryError as exc:
        raise GitOperationError("find_repo_root", "Not inside a git repository") from exc
    finally:
        _close_repo(repo)


def find_main_worktree_root(start: Path | str = Path()) -> Path:
    """Find the primary worktree root for the current repository.

    For linked worktrees, this resolves to the main checkout that owns the
    shared git common directory. For ordinary repositories, it matches the
    active repository root.

    This helper detects linked git worktrees only as a workspace-root resolver
    and is NEVER used by the same-workspace parallel worker path. Parallel v1
    workers always share the canonical repo_root; this function MUST NOT be
    invoked by ``ralph.pipeline.parallel.*`` modules. Callers in that package
    violate the same-workspace isolation contract.
    """
    repo: Repo | None = None
    try:
        repo = Repo(start, search_parent_directories=True)
        common_dir = Path(repo.common_dir).resolve()
        return common_dir.parent.resolve()
    except InvalidGitRepositoryError as exc:
        raise GitOperationError("find_main_worktree_root", "Not inside a git repository") from exc
    finally:
        _close_repo(repo)


def is_repo_clean(repo_root: Path | str) -> bool:
    """Check if repository has uncommitted changes.

    Args:
        repo_root: Path to the repository root.

    Returns:
        True if repository is clean (no uncommitted changes).
    """
    repo_root_path = Path(repo_root)
    try:
        result = run_git(
            (*COMMIT_PIN_CONFIG_ARGS, "status", "--porcelain", "--untracked-files=no"),
            cwd=repo_root_path,
            label="git-status",
        )
        if result.returncode == 0:
            return not bool(result.stdout.splitlines())
    except OSError:
        pass

    repo: Repo | None = None
    try:
        repo = Repo(repo_root_path)
        return not repo.is_dirty()
    finally:
        _close_repo(repo)


def has_uncommitted_changes(repo_root: Path | str) -> bool:
    """Return True when the working tree has uncommitted work.

    Includes staged diff, unstaged diff, and untracked files. This is the
    authoritative skip check for commit phases: if this returns False, there
    is literally nothing for a commit agent to package up.
    """
    repo_root_path = Path(repo_root)
    try:
        result = run_git(("status", "--porcelain"), cwd=repo_root_path, label="git-status")
        if result.returncode == 0:
            return bool(result.stdout.splitlines())
    except OSError:
        pass

    repo: Repo | None = None
    try:
        repo = Repo(repo_root_path)
        return repo.is_dirty(untracked_files=True)
    finally:
        _close_repo(repo)


def has_commits_since(repo_root: Path | str, baseline_sha: str | None) -> bool:
    """Return True when HEAD is ahead of ``baseline_sha``.

    When ``baseline_sha`` is None the caller has no prior baseline (first run),
    so we conservatively return True to allow the caller to proceed.
    """
    if baseline_sha is None:
        return True
    repo_root_path = Path(repo_root)
    try:
        result = run_git(
            ("rev-list", "--max-count=1", f"{baseline_sha}..HEAD"),
            cwd=repo_root_path,
            label="git-rev-list",
        )
        if result.returncode == 0:
            return bool(result.stdout.strip())
    except OSError:
        pass

    repo: Repo | None = None
    try:
        repo = Repo(repo_root_path)
        return any(True for _ in repo.iter_commits(f"{baseline_sha}..HEAD"))
    except Exception:
        return True
    finally:
        _close_repo(repo)


def has_staged_changes(repo_root: Path | str) -> bool:
    """Check if repository has staged changes.

    Args:
        repo_root: Path to the repository root.

    Returns:
        True if there are staged changes. Untracked-only worktrees
        return False -- this helper is the staged-only contract used by
        ``commit`` to decide whether a commit can proceed.
    """
    try:
        status_lines = _git_status_porcelain_lines(Path(repo_root))
    except (OSError, GitOperationError):
        repo: Repo | None = None
        try:
            repo = Repo(repo_root)
            return bool(repo.index.diff("HEAD"))
        finally:
            _close_repo(repo)
    return any(_line_is_staged(line) for line in status_lines)


def list_changed_paths(repo_root: Path | str) -> list[str]:
    """Return unique dirty paths from ``git status --porcelain`` in output order.

    Commit-message scopes are resolved before their paths are staged, so this
    deliberately includes both staged and unstaged changes (including
    untracked files). ``get_staged_files`` remains the staged-only API.
    """
    try:
        status_lines = _git_status_porcelain_lines(Path(repo_root))
    except (OSError, GitOperationError):
        repo: Repo | None = None
        try:
            repo = Repo(repo_root)
            status_lines = cast(
                "str", repo.git.status("--porcelain")
            ).splitlines()  # cast-policy: seam: structural boundary (sqlite Row / lazy module attr / protocol conferee)
        finally:
            _close_repo(repo)

    changed_paths: list[str] = []
    for line in status_lines:
        if len(line) <= _PORCELAIN_STATUS_PREFIX_LEN:
            continue
        path_part = line[_PORCELAIN_STATUS_PREFIX_LEN:]
        if " -> " in path_part:
            _, _, path_part = path_part.partition(" -> ")
        path = path_part.strip()
        if path and path not in changed_paths:
            changed_paths.append(path)
    return changed_paths


def get_staged_files(repo_root: Path | str) -> list[str]:
    """Get list of staged files.

    Args:
        repo_root: Path to the repository root.

    Returns:
        List of staged file paths. Untracked-only worktrees return ``[]``.
    """
    try:
        status_lines = _git_status_porcelain_lines(Path(repo_root))
    except (OSError, GitOperationError):
        repo: Repo | None = None
        try:
            repo = Repo(repo_root)
            staged = repo.index.diff("HEAD")
            return [item.a_path for item in staged if item.a_path] if staged else []
        finally:
            _close_repo(repo)

    staged_paths: list[str] = []
    for line in status_lines:
        if not _line_is_staged(line) or len(line) <= _PORCELAIN_STATUS_PREFIX_LEN:
            continue
        path_part = line[_PORCELAIN_STATUS_PREFIX_LEN:]
        if " -> " in path_part:
            _, _, path_part = path_part.partition(" -> ")
        path = path_part.strip()
        if path and path not in staged_paths:
            staged_paths.append(path)
    return staged_paths


def _line_is_staged(line: str) -> bool:
    """Return True iff ``line`` (from ``git status --porcelain``) represents a staged change.

    Untracked files (``??``) are explicitly excluded -- the staged-only
    contract does NOT include them. ``has_uncommitted_changes`` is the
    API for the broader dirty-state check.
    """
    if not line:
        return False
    if line.startswith("??"):
        return False
    return line[0] not in {" ", "?"}


def _git_status_porcelain_lines(repo_root: Path) -> list[str]:
    """Run ``git status --porcelain`` and return the split output lines.

    Raises:
        GitOperationError: when ``git status --porcelain`` exits with a
            non-zero return code. Earlier versions silently collapsed a
            non-zero return into ``[]`` which made downstream helpers
            (``has_staged_changes`` / ``list_changed_paths`` /
            ``get_staged_files``) report a false-clean repo. The
            failure now surfaces so callers can route through the
            GitPython fallback path or fail loud.
    """
    result = run_git(("status", "--porcelain"), cwd=repo_root, label="git-status")
    if result.returncode != 0:
        raise GitOperationError(
            "git-status",
            f"git status --porcelain failed (rc={result.returncode}): "
            f"{result.stderr.strip() or '<no stderr>'}",
        )
    return result.stdout.splitlines()


def stage_all(repo_root: Path | str) -> None:
    """Stage all changes (git add -A).

    Args:
        repo_root: Path to the repository root.
    """
    repo: Repo | None = None
    try:
        repo = Repo(repo_root)

        def _stage() -> None:
            _ = cast(
                "str", repo.git.add(A=True)
            )  # cast-policy: seam: structural boundary (sqlite Row / lazy module attr / protocol conferee)

        _run_git_operation_with_stale_lock_recovery("stage_all", _stage)
        logger.debug("Staged all changes in {}", repo_root)
    except Exception as exc:
        raise GitOperationError("stage_all", str(exc)) from exc
    finally:
        _close_repo(repo)


def stage_files(repo_root: Path | str, files: list[str]) -> None:
    """Stage only the provided repository-relative paths.

    Uses ``git add --all -- <paths>`` so modified, untracked, and deleted files
    are all handled consistently for the selected scope.
    """
    if not files:
        logger.debug("No files requested for selective staging in {}", repo_root)
        return
    repo: Repo | None = None
    try:
        repo = Repo(repo_root)

        def _stage() -> None:
            _ = cast(
                "str", repo.git.add("--all", "--", *files)
            )  # cast-policy: seam: structural boundary (sqlite Row / lazy module attr / protocol conferee)

        _run_git_operation_with_stale_lock_recovery("stage_files", _stage)
        logger.debug("Staged {} selected paths in {}", len(files), repo_root)
    except Exception as exc:
        raise GitOperationError("stage_files", str(exc)) from exc
    finally:
        _close_repo(repo)


def create_commit(
    repo_root: Path | str,
    message: str,
    *,
    author_name: str | None = None,
    author_email: str | None = None,
    expected_head: str,
) -> CommitCreationResult:
    """Create a git commit.

    Args:
        repo_root: Path to the repository root.
        message: Commit message.
        author_name: Optional author name override.
        author_email: Optional author email override.
        expected_head: HEAD SHA required for the ref compare-and-swap.

    Returns:
        Typed result describing commit creation.

    Raises:
        GitOperationError: If commit fails.
    """
    repo: Repo | None = None
    try:
        repo = Repo(repo_root)
        message = _append_ralph_workflow_coauthor_trailer(message)
        if hasattr(repo, "head") and repo.head.commit.hexsha != expected_head:
            return CommitCreationResult.already_advanced(repo.head.commit.hexsha)
        if not hasattr(repo, "git_dir"):
            author_name, author_email = _resolve_commit_identity(repo, author_name, author_email)
            actor = Actor(author_name, author_email)
            commit = _run_git_operation_with_stale_lock_recovery(
                "create_commit",
                lambda: repo.index.commit(message, author=actor, committer=actor),
            )
            return CommitCreationResult.created(commit.hexsha)
        author_name, author_email = _resolve_commit_identity(repo, author_name, author_email)

        ref_result = run_git(
            ("symbolic-ref", "--quiet", "HEAD"),
            cwd=Path(repo_root),
            label="git-symbolic-ref-head",
        )
        if ref_result.returncode != 0 or not ref_result.stdout.strip():
            raise GitOperationError("create_commit", "HEAD is detached")
        ref_name = ref_result.stdout.strip()

        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", delete=False) as message_file:
            message_file.write(message)
            message_path = Path(message_file.name)
        try:
            hook_env = {"GIT_REFLOG_ACTION": "ralph create_commit"}
            hooks_dir = Path(repo.git_dir) / "hooks"
            _run_commit_hooks(Path(repo_root), hooks_dir, message_path, hook_env)
            # filesystem-read-ok: Git hooks mutate this owned temporary commit-message file in place
            message = message_path.read_text(encoding="utf-8")
        finally:
            message_path.unlink(  # filesystem-write-ok: transient hook message scratch
                missing_ok=True
            )

        tree_result = run_git(("write-tree",), cwd=Path(repo_root), label="git-write-tree")
        if tree_result.returncode != 0:
            raise GitOperationError("create_commit", tree_result.stderr.strip())
        commit_result = run_git(
            ("commit-tree", tree_result.stdout.strip(), "-p", expected_head),
            cwd=Path(repo_root),
            label="git-commit-tree",
            options=GitRunOptions(
                env={
                    "GIT_AUTHOR_NAME": author_name,
                    "GIT_AUTHOR_EMAIL": author_email,
                    "GIT_COMMITTER_NAME": author_name,
                    "GIT_COMMITTER_EMAIL": author_email,
                    "GIT_REFLOG_ACTION": "ralph create_commit",
                },
                input_data=message,
            ),
        )
        if commit_result.returncode != 0:
            raise GitOperationError("create_commit", commit_result.stderr.strip())
        candidate_sha = commit_result.stdout.strip()
        update_ref = run_git(
            ("update-ref", ref_name, candidate_sha, expected_head),
            cwd=Path(repo_root),
            label="git-update-ref-cas",
            options=GitRunOptions(env={"GIT_REFLOG_ACTION": "ralph create_commit"}),
        )
        if update_ref.returncode != 0:
            current_head = get_head_sha(repo_root)
            if current_head != expected_head:
                return CommitCreationResult.already_advanced(current_head)
            detail = update_ref.stderr.strip() or "git update-ref failed without diagnostics"
            raise GitOperationError("create_commit", detail)
        post_commit = hooks_dir / "post-commit"
        if post_commit.exists():
            post_result = run_git(
                ("hook", "run", "post-commit"),
                cwd=Path(repo_root),
                label="git-hook-post-commit",
                options=GitRunOptions(env=hook_env),
            )
            if post_result.returncode != 0:
                logger.warning(
                    "post-commit hook failed after commit {}: {}",
                    candidate_sha[:8],
                    post_result.stderr.strip() or "no diagnostics",
                )
        logger.info(
            "Created commit {}: {}",
            candidate_sha[:8],
            message.splitlines()[0] if message else "(no message)",
        )
        return CommitCreationResult.created(candidate_sha)
    except Exception as exc:
        raise GitOperationError("create_commit", str(exc)) from exc
    finally:
        _close_repo(repo)


def push(
    repo_root: Path | str,
    remote: str,
    branch: str | None = None,
) -> None:
    """Push current branch to remote.

    Args:
        repo_root: Path to the repository root.
        remote: Remote name to push to.
        branch: Optional branch name override.

    Raises:
        GitOperationError: If push fails.
    """
    repo: Repo | None = None
    try:
        repo = Repo(repo_root)
        active_branch = branch or repo.active_branch.name
        repo.remote(remote).push(active_branch)
        logger.info("Pushed {} to {}/{}", active_branch, remote, active_branch)
    except Exception as exc:
        raise GitOperationError("push", str(exc)) from exc
    finally:
        _close_repo(repo)


def get_head_sha(repo_root: Path | str) -> str:
    """Return current HEAD commit SHA.

    Args:
        repo_root: Path to the repository root.

    Returns:
        SHA of the current HEAD commit.

    Raises:
        GitOperationError: If HEAD cannot be read.

    The wrapping is deliberate and matches :func:`find_repo_root` and
    :func:`merge_base` in this module. Raw GitPython exceptions used to
    escape into the auto-integrate skip table, where they were recorded
    as an opaque ``unexpected failure`` that named neither the operation
    nor the repository.
    """
    repo: Repo | None = None
    try:
        repo = Repo(repo_root)
        return repo.head.commit.hexsha
    except Exception as exc:
        raise GitOperationError("get_head_sha", str(exc)) from exc
    finally:
        _close_repo(repo)


def merge_base(
    repo_root: Path | str,
    ref_a: str,
    ref_b: str,
) -> str:
    """Return merge-base SHA between two refs.

    Args:
        repo_root: Path to the repository root.
        ref_a: First ref (branch, tag, SHA).
        ref_b: Second ref (branch, tag, SHA).

    Returns:
        SHA of the merge base commit.

    Raises:
        GitOperationError: If merge base cannot be determined.
    """
    repo: Repo | None = None
    try:
        repo = Repo(repo_root)
        bases = repo.merge_base(ref_a, ref_b)
        if not bases:
            msg = f"No merge base between {ref_a} and {ref_b}"
            raise GitOperationError("merge_base", msg)
        return bases[0].hexsha
    except Exception as exc:
        raise GitOperationError("merge_base", str(exc)) from exc
    finally:
        _close_repo(repo)


def get_current_branch(repo_root: Path | str) -> str:
    """Get the current branch name.

    Args:
        repo_root: Path to the repository root.

    Returns:
        Name of the current branch.
    """
    repo: Repo | None = None
    try:
        repo = Repo(repo_root)
        return repo.active_branch.name
    finally:
        _close_repo(repo)


def get_commits_between(
    repo_root: Path | str,
    from_ref: str,
    to_ref: str,
) -> list[str]:
    """Get list of commit SHAs between two refs.

    Args:
        repo_root: Path to the repository root.
        from_ref: Starting ref (exclusive).
        to_ref: Ending ref (inclusive).

    Returns:
        List of commit SHAs in reverse chronological order.
    """
    repo: Repo | None = None
    try:
        repo = Repo(repo_root)
        commits = repo.iter_commits(f"{from_ref}..{to_ref}")
        return [c.hexsha for c in commits]
    finally:
        _close_repo(repo)


def append_to_gitignore(repo_root: Path | str, patterns: list[str]) -> None:
    """Append patterns to .gitignore.

    Args:
        repo_root: Path to the repository root.
        patterns: List of patterns to add.
    """
    gitignore_path = Path(repo_root) / ".gitignore"
    existing = set()
    if gitignore_path.exists():
        existing = set(gitignore_path.read_text().splitlines())

    new_patterns = [p for p in patterns if p not in existing]
    if new_patterns:
        payload = ("\n".join(new_patterns)) + "\n"
        _atomic_append_text(gitignore_path, payload)
        logger.debug("Appended {} patterns to .gitignore", len(new_patterns))


def _atomic_append_text(
    path: Path,
    payload: str,
    *,
    encoding: str = "utf-8",
) -> None:
    """Append ``payload`` to ``path`` atomically via sibling-staging + ``Path.replace``.

    Mirrors the pattern from ``ralph/mcp/transport/agy.py:99-127`` so a
    SIGKILL mid-write leaves the target file intact -- the new content is
    staged in a sibling file (with a sha256(payload)-derived hash plus
    os.getpid() suffix), then ``Path.replace()``-published atomically.

    Boundary normalization: when ``path`` exists and its content does NOT
    end with ``\\n``, the helper inserts a single ``\\n`` separator before
    the appended payload. Without this, appending ``"*.cache\\n"`` to a
    file containing ``"existing-without-newline"`` would publish
    ``"existing-without-newline*.cache\\n"`` -- a malformed single-line
    rule rather than two separate rules.

    Byte-preserving round-trip: the helper reads the existing file via
    ``Path.read_bytes()`` and writes the staged file via
    ``Path.write_bytes()``. This preserves CRLF (``\\r\\n``), BOM, and
    any other byte-level content the caller wrote -- a text-mode round
    trip via ``read_text``/``write_text`` would normalize CRLF to LF
    on POSIX (universal-newlines mode) and silently corrupt
    Windows-style or gitattributes-style content. The payload string
    is encoded with the same ``encoding`` argument for the write so the
    boundary insert (when needed) does not double-encode the existing
    bytes.

    Read-failure policy: when ``path.exists()`` is True but the read
    raises ``OSError`` (permission denied, broken FS, transient I/O), the
    helper fails closed by re-raising the underlying ``OSError``. A
    silent fallback to ``existing = ""`` would publish ``payload`` over
    the original content via ``Path.replace()`` -- exactly the silent
    corruption the atomic helper exists to prevent. Callers that want a
    best-effort fallback (e.g. bootstrap auto-seed) wrap the call in
    their own try/except. A missing ``path`` is treated as empty
    (legitimate creation case).

    If the staging write or replace fails, the staging file is removed
    before re-raising so the helper never leaves a dangling sibling.

    Args:
        path: Target file path. Parent directory must already exist (or
            be creatable via ``path.parent.mkdir(parents=True, exist_ok=True)``).
        payload: Text to append. The helper normalizes the boundary
            against the existing content (see above).
        encoding: Text encoding for the staging write. Default ``utf-8``.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = b""
    if path.exists():
        existing = path.read_bytes()
    separator = b""
    if existing and not existing.endswith(b"\n"):
        separator = b"\n"
    _staging_hash = hashlib.sha256(payload.encode(encoding)).hexdigest()[:16]
    _staging_pid = os.getpid()
    staging = path.with_suffix(path.suffix + f".ralph-staging.{_staging_pid}.{_staging_hash}")
    try:
        # filesystem-write-ok: temp staging file (atomic replace target below)
        staging.write_bytes(existing + separator + payload.encode(encoding))
        staging.replace(path)
    except BaseException:
        with suppress(FileNotFoundError):
            staging.unlink()
        raise


def atomic_append_text(
    path: Path,
    payload: str,
    *,
    encoding: str = "utf-8",
) -> None:
    """Append text through Ralph's atomic sibling-staging publication path."""
    _atomic_append_text(path, payload, encoding=encoding)
