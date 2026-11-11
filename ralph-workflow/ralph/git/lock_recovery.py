
from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from ralph.git._lock_recovery import (
    LIVE_HOLDER_PATIENCE_S,
    LIVE_HOLDER_POLL_S,
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
    return _lock_path_is_reapable(lock_path)


def parse_lock_path_from_error(error_text: str, base_dir: Path | None = None) -> Path | None:
    return _parse_lock_path_from_error(error_text, base_dir)


def gitdir_for_lock(lock_path: Path) -> Path:
    return _gitdir_for_lock(lock_path)


def lock_identity(lock_path: Path) -> tuple[int, int, int] | None:
    return _lock_identity(lock_path)


def unlink_if_unchanged(lock_path: Path, probed_identity: tuple[int, int, int] | None) -> bool:
    return _unlink_if_unchanged(lock_path, probed_identity)


def rule_out_unreadable(
    proc_root: Path,
    pid_str: str,
    uids: tuple[int, ...] | None,
    lock_uid: int | None,
    lock_age_s: float | None,
) -> Literal["dead", "unknown"]:
    return _rule_out_unreadable(proc_root, pid_str, uids, lock_uid, lock_age_s)


def pid_in_repo_cwd(
    pid_dir: Path,
    live_cwds: set[Path],
    common_worktrees_dir: Path,
) -> Literal["in_repo", "not_in_repo", "unknown"]:
    return _pid_in_repo_cwd(pid_dir, live_cwds, common_worktrees_dir)


def pid_has_gitdir_environ(
    pid_dir: Path,
    gitdir: bytes | Path,
    gitdir_common: bytes | Path,
) -> Literal["matches", "no_match", "unknown"]:
    return _pid_has_gitdir_environ(pid_dir, gitdir, gitdir_common)


__all__ = [
    "LIVE_HOLDER_PATIENCE_S",
    "LIVE_HOLDER_POLL_S",
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
