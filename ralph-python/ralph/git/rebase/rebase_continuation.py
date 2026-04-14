"""Helpers for continuing paused Git rebases."""

from __future__ import annotations

import subprocess
import os
from pathlib import Path
from typing import Sequence

from git import GitCommandError, InvalidGitRepositoryError, Repo

from ralph.git.operations import GitOperationError, find_repo_root

__all__ = [
    "ConflictRemainingError",
    "NoRebaseInProgressError",
    "RebaseContinuationError",
    "RebaseVerificationError",
    "continue_rebase",
    "continue_rebase_at",
    "rebase_in_progress",
    "rebase_in_progress_at",
    "verify_rebase_completed",
    "verify_rebase_completed_at",
]

_REBASE_INDICATORS: Sequence[str] = ("rebase-apply", "rebase-merge")


def _git_env() -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault("GIT_EDITOR", ":")
    env.setdefault("EDITOR", ":")
    env.setdefault("VISUAL", ":")
    env.setdefault("GIT_SEQUENCE_EDITOR", ":")
    return env


class RebaseContinuationError(Exception):
    """Base exception for rebase continuation helpers."""


class NoRebaseInProgressError(RebaseContinuationError):
    """Raised when no rebase is active but continuation was requested."""


class ConflictRemainingError(RebaseContinuationError):
    """Raised when conflicts remain while attempting to continue."""


class RebaseVerificationError(Exception):
    """Raised when verifying rebase completion fails."""


def _open_repo(repo_root: Path | str) -> Repo:
    try:
        return Repo(repo_root)
    except InvalidGitRepositoryError as exc:
        raise RebaseContinuationError(
            "Repository root is invalid or not a git repository"
        ) from exc


def _resolve_repo_root(repo_root: Path | str | None = None) -> Path:
    if repo_root:
        return Path(repo_root)
    try:
        return find_repo_root()
    except GitOperationError as exc:
        raise RebaseContinuationError("Unable to locate git repository") from exc


def _git_dir(repo: Repo) -> Path:
    git_dir = repo.git_dir
    if not git_dir:
        raise RebaseContinuationError("Git directory could not be determined")
    return Path(git_dir)


def _rebase_in_progress_impl(repo: Repo) -> bool:
    git_dir = _git_dir(repo)
    return any((git_dir / indicator).exists() for indicator in _REBASE_INDICATORS)


def _repo_root_path(repo: Repo) -> Path:
    if repo.working_tree_dir:
        return Path(repo.working_tree_dir)
    return _git_dir(repo).parent


def _has_index_conflicts(repo: Repo) -> bool:
    repo_root = _repo_root_path(repo)
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", "--diff-filter=U"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            check=True,
            env=_git_env(),
        )
        return bool(result.stdout.strip())
    except (subprocess.CalledProcessError, OSError) as exc:
        raise RebaseContinuationError("Unable to inspect git index") from exc


def rebase_in_progress_at(repo_root: Path | str) -> bool:
    repo = _open_repo(repo_root)
    return _rebase_in_progress_impl(repo)


def rebase_in_progress(repo_root: Path | str | None = None) -> bool:
    path = _resolve_repo_root(repo_root)
    return rebase_in_progress_at(path)


def verify_rebase_completed_at(repo_root: Path | str, upstream_branch: str) -> bool:
    repo = _open_repo(repo_root)

    if _rebase_in_progress_impl(repo):
        return False

    try:
        if _has_index_conflicts(repo):
            return False
    except RebaseContinuationError as exc:
        raise RebaseVerificationError("Unable to inspect index for conflicts") from exc

    if repo.head.is_detached:
        raise RebaseVerificationError("Repository HEAD is detached")

    try:
        _ = repo.commit(upstream_branch)
    except (GitCommandError, ValueError) as exc:
        raise RebaseVerificationError("Upstream branch is invalid") from exc

    if not _head_is_descendant(repo_root, upstream_branch):
        return False

    return True


def verify_rebase_completed(upstream_branch: str, repo_root: Path | str | None = None) -> bool:
    path = _resolve_repo_root(repo_root)
    return verify_rebase_completed_at(path, upstream_branch)


def continue_rebase_at(repo_root: Path | str) -> None:
    repo = _open_repo(repo_root)

    if not _rebase_in_progress_impl(repo):
        raise NoRebaseInProgressError("No rebase in progress")

    if _has_index_conflicts(repo):
        raise ConflictRemainingError("Conflicts still exist in the index")

    try:
        subprocess.run(
            ["git", "rebase", "--continue"],
            cwd=str(Path(repo_root)),
            check=True,
            capture_output=True,
            text=True,
            env=_git_env(),
        )
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.strip() if exc.stderr else exc.stdout.strip() if exc.stdout else str(exc)
        raise RebaseContinuationError(
            f"Failed to continue rebase: {stderr}"
        ) from exc


def continue_rebase(repo_root: Path | str | None = None) -> None:
    path = _resolve_repo_root(repo_root)
    continue_rebase_at(path)


def _head_is_descendant(repo_root: Path | str, upstream_branch: str) -> bool:
    repo_root_path = Path(repo_root)
    result = subprocess.run(
        ["git", "merge-base", "--is-ancestor", upstream_branch, "HEAD"],
        cwd=str(repo_root_path),
        capture_output=True,
        text=True,
        env=_git_env(),
    )
    if result.returncode == 0:
        return True
    if result.returncode == 1:
        return False
    raise RebaseVerificationError(
        f"git merge-base failed: {result.stderr.strip() or result.stdout.strip()}"
    )
