"""Public Git lock recovery module.

This facade is the public surface for every Git stale-lock operation: path
reapability, lock identity, error-message parsing, liveness probing and the
retry wrapper. The implementation lives in the private
:mod:`ralph.git._lock_recovery` module; the names re-exported here are the
supported, documented entry points.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from ralph.git._lock_recovery import (
    LOCK_BACKOFF_S,
    PROBE_MAX_WORKERS,
    ProbeExecutorStatus,
    _gitdir_for_lock,
    _lock_identity,
    _lock_path_is_reapable,
    _parse_lock_path_from_error,
    _pid_has_gitdir_environ,
    _pid_in_repo_cwd,
    _rule_out_unreadable,
    _unlink_if_unchanged,
    git_lock_candidates,
    lock_has_no_live_holder,
    names_a_lock_file,
    probe_executor_status,
    probe_pool_capacity,
    reap_stale_locks,
    recover_stale_git_lock,
    reset_probe_executor,
    run_git_operation_with_stale_lock_recovery,
)
from ralph.git._run_git_lock_recovery import git_subcommand, run_with_lock_recovery

if TYPE_CHECKING:
    from pathlib import Path


def lock_path_is_reapable(lock_path: Path) -> bool:
    """Return whether the path is a stale Git lock Ralph is allowed to reap.

    A path is reapable only when it is a known Git lock file that lives inside
    a Git directory and is not owned by Ralph itself (lease / auto-integrate
    locks are never reaped).
    """
    return _lock_path_is_reapable(lock_path)


def parse_lock_path_from_error(error_text: str, base_dir: Path | None = None) -> Path | None:
    """Return the lock path a Git error message names, or None when it names none.

    Relative lock paths are resolved against ``base_dir``; paths that escape
    ``base_dir`` are refused (``None``).
    """
    return _parse_lock_path_from_error(error_text, base_dir)


def gitdir_for_lock(lock_path: Path) -> Path:
    """Return the enclosing Git directory that owns the given lock path."""
    return _gitdir_for_lock(lock_path)


def lock_identity(lock_path: Path) -> tuple[int, int, int] | None:
    """Return the ``(st_dev, st_ino, st_mtime_ns)`` identity of a lock file.

    ``None`` is returned when the lock no longer exists on disk.
    """
    return _lock_identity(lock_path)


def unlink_if_unchanged(lock_path: Path, probed_identity: tuple[int, int, int] | None) -> bool:
    """Remove a lock file only when it is still the exact file that was probed.

    The identity is re-checked immediately before removal; a lock that was
    replaced in the meantime is left untouched and ``False`` is returned.
    """
    return _unlink_if_unchanged(lock_path, probed_identity)


def rule_out_unreadable(
    proc_root: Path,
    pid_str: str,
    uids: tuple[int, ...] | None,
    lock_uid: int | None,
    lock_age_s: float | None,
) -> Literal["dead", "unknown"]:
    """Decide an unreadable process using only sound facts.

    ``"dead"`` is returned only when an affirmative fact proves the process
    cannot hold the lock (foreign uid, non-git process on an aged lock);
    otherwise the verdict fails closed to ``"unknown"``.
    """
    return _rule_out_unreadable(proc_root, pid_str, uids, lock_uid, lock_age_s)


def pid_in_repo_cwd(
    pid_dir: Path,
    live_cwds: set[Path],
    common_worktrees_dir: Path,
) -> Literal["in_repo", "not_in_repo", "unknown"]:
    """Return whether a process' working directory sits inside the repository."""
    return _pid_in_repo_cwd(pid_dir, live_cwds, common_worktrees_dir)


def pid_has_gitdir_environ(
    pid_dir: Path,
    gitdir: bytes | Path,
    gitdir_common: bytes | Path,
) -> Literal["matches", "no_match", "unknown"]:
    """Return whether a process environment points ``GIT_DIR`` at the repository."""
    return _pid_has_gitdir_environ(pid_dir, gitdir, gitdir_common)


__all__ = [
    "LOCK_BACKOFF_S",
    "PROBE_MAX_WORKERS",
    "ProbeExecutorStatus",
    "git_lock_candidates",
    "git_subcommand",
    "gitdir_for_lock",
    "lock_has_no_live_holder",
    "lock_identity",
    "lock_path_is_reapable",
    "names_a_lock_file",
    "parse_lock_path_from_error",
    "pid_has_gitdir_environ",
    "pid_in_repo_cwd",
    "probe_executor_status",
    "probe_pool_capacity",
    "reap_stale_locks",
    "recover_stale_git_lock",
    "reset_probe_executor",
    "rule_out_unreadable",
    "run_git_operation_with_stale_lock_recovery",
    "run_with_lock_recovery",
    "unlink_if_unchanged",
]
