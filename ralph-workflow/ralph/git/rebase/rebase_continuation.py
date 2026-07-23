"""Helpers for continuing paused Git rebases."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, cast

from git import GitCommandError, InvalidGitRepositoryError, Repo

from ralph.git.hardening import PINNED_CONFIG_ARGS, scrub_git_env
from ralph.git.operations import GitOperationError, find_repo_root
from ralph.git.rebase._conflict_remaining_error import ConflictRemainingError
from ralph.git.rebase._no_rebase_in_progress_error import NoRebaseInProgressError
from ralph.git.rebase._rebase_continuation_error import RebaseContinuationError
from ralph.git.rebase.rebase import is_empty_rebase_stop
from ralph.git.subprocess_runner import GitRunOptions, run_git

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

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
_MAX_EMPTY_SKIP_ATTEMPTS: int = 20


def _git_env() -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault("GIT_EDITOR", ":")
    env.setdefault("EDITOR", ":")
    env.setdefault("VISUAL", ":")
    env.setdefault("GIT_SEQUENCE_EDITOR", ":")
    return env


class RebaseVerificationError(Exception):
    """Raised when verifying rebase completion fails."""


def _open_repo(repo_root: Path | str) -> Repo:
    try:
        return Repo(repo_root)
    except InvalidGitRepositoryError as exc:
        raise RebaseContinuationError("Repository root is invalid or not a git repository") from exc


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
        result = run_git(
            ["diff", "--name-only", "--diff-filter=U"],
            cwd=repo_root,
            label="git-rebase:diff",
            options=GitRunOptions(env=_git_env(), check=True),
        )
        return bool(result.stdout.strip())
    except subprocess.CalledProcessError as exc:
        raise RebaseContinuationError("Unable to inspect git index") from exc


def rebase_in_progress_at(repo_root: Path | str) -> bool:
    """Return True if a git rebase is currently in progress at ``repo_root``."""
    repo = open_repo(repo_root)
    try:
        return rebase_in_progress_impl(repo)
    finally:
        _close_repo(repo)


def rebase_in_progress(repo_root: Path | str | None = None) -> bool:
    """Return True if a git rebase is in progress, auto-detecting the repo root."""
    path = _resolve_repo_root(repo_root)
    return rebase_in_progress_at(path)


def verify_rebase_completed_at(repo_root: Path | str, upstream_branch: str) -> bool:
    """Return True if the rebase is complete and HEAD is a descendant of ``upstream_branch``."""
    repo = open_repo(repo_root)
    try:
        if rebase_in_progress_impl(repo):
            return False

        try:
            if has_index_conflicts(repo):
                return False
        except RebaseContinuationError as exc:
            raise RebaseVerificationError("Unable to inspect index for conflicts") from exc

        if repo.head.is_detached:
            raise RebaseVerificationError("Repository HEAD is detached")

        try:
            _ = repo.commit(upstream_branch)
        except (GitCommandError, ValueError) as exc:
            raise RebaseVerificationError("Upstream branch is invalid") from exc

        return head_is_descendant(repo_root, upstream_branch)
    finally:
        _close_repo(repo)


def verify_rebase_completed(upstream_branch: str, repo_root: Path | str | None = None) -> bool:
    """Verify rebase completion, auto-detecting the repo root."""
    path = _resolve_repo_root(repo_root)
    return verify_rebase_completed_at(path, upstream_branch)


def continue_rebase_at(repo_root: Path | str) -> None:
    """Resume a paused rebase at ``repo_root``, raising if conflicts remain."""
    repo = open_repo(repo_root)
    try:
        if not rebase_in_progress_impl(repo):
            raise NoRebaseInProgressError("No rebase in progress")

        if has_index_conflicts(repo):
            raise ConflictRemainingError("Conflicts still exist in the index")
    finally:
        _close_repo(repo)

    try:
        # ``git rebase --continue`` is a START-TIME-INDEPENDENT verb:
        # the only flags git accepts with it are the engine-control
        # ones (``--no-verify``, ``--skip``/``--quit`` etc.), NOT the
        # replay-shape flags (``--no-autostash``, ``--no-autosquash``,
        # ``--no-update-refs``, ``--empty=...``) which only apply to
        # the initial rebase invocation. Passing those flags with
        # ``--continue`` makes real git print a usage error and exit
        # non-zero (the rebase-conflict loop never gets past stop 1).
        # The replay-shape policy is therefore carried by the original
        # ``rebase_onto`` call's argv; the continuation just needs the
        # pinned -c config and the non-interactive editor env, both of
        # which :data:`PINNED_CONFIG_ARGS` and ``_git_env()`` already
        # supply. The ``GIT_EDITOR=`` / ``GIT_SEQUENCE_EDITOR=`` env
        # vars are pinned unconditionally in
        # :data:`_GIT_BATCH_MODE_ENV` so the only failure mode left is
        # a hung edit, which the universal git call timeout closes.
        # ``--no-verify`` is intentionally OMITTED so a user-installed
        # ``pre-rebase`` / ``reference-transaction`` hook still has a
        # chance to veto a malicious replay: D2 classifies such a
        # refusal as a clean retryable error.
        run_git(
            [*PINNED_CONFIG_ARGS, "rebase", "--continue"],
            cwd=Path(repo_root),
            label="git-rebase:continue",
            options=GitRunOptions(env=scrub_git_env(_git_env()), check=True),
        )
    except subprocess.CalledProcessError as exc:
        raw_stderr: object = exc.stderr
        raw_stdout: object = exc.stdout
        stderr = raw_stderr if isinstance(raw_stderr, str) else ""
        stdout = raw_stdout if isinstance(raw_stdout, str) else ""
        if not is_empty_rebase_stop(stderr, stdout) or not rebase_in_progress_at(repo_root):
            detail = stderr.strip() or stdout.strip() or str(exc)
            raise RebaseContinuationError(f"Failed to continue rebase: {detail}") from exc

        # C15 continuation-path half: a resolver can make this replayed
        # commit empty, so skip it just as the initial rebase path does.
        for _ in range(_MAX_EMPTY_SKIP_ATTEMPTS):
            skipped = run_git(
                [*PINNED_CONFIG_ARGS, "rebase", "--skip"],
                cwd=Path(repo_root),
                label="git-rebase:skip",
                options=GitRunOptions(env=scrub_git_env(_git_env())),
            )
            if not rebase_in_progress_at(repo_root):
                return
            if skipped.returncode != 0 and not is_empty_rebase_stop(
                skipped.stderr, skipped.stdout
            ):
                detail = skipped.stderr.strip() or skipped.stdout.strip() or "git rebase --skip failed"
                raise RebaseContinuationError(f"Failed to continue rebase: {detail}") from exc
            try:
                run_git(
                    [*PINNED_CONFIG_ARGS, "rebase", "--continue"],
                    cwd=Path(repo_root),
                    label="git-rebase:continue",
                    options=GitRunOptions(env=scrub_git_env(_git_env()), check=True),
                )
                return
            except subprocess.CalledProcessError as next_exc:
                raw_next_stderr: object = next_exc.stderr
                raw_next_stdout: object = next_exc.stdout
                next_stderr = raw_next_stderr if isinstance(raw_next_stderr, str) else ""
                next_stdout = raw_next_stdout if isinstance(raw_next_stdout, str) else ""
                if not is_empty_rebase_stop(next_stderr, next_stdout):
                    detail = next_stderr.strip() or next_stdout.strip() or str(next_exc)
                    raise RebaseContinuationError(f"Failed to continue rebase: {detail}") from next_exc
                if not rebase_in_progress_at(repo_root):
                    return

        raise RebaseContinuationError("Failed to continue rebase: empty commit skip limit exceeded") from exc


def continue_rebase(repo_root: Path | str | None = None) -> None:
    """Resume a paused rebase, auto-detecting the repo root."""
    path = _resolve_repo_root(repo_root)
    continue_rebase_at(path)


def _head_is_descendant(repo_root: Path | str, upstream_branch: str) -> bool:
    repo_root_path = Path(repo_root)
    result = run_git(
        ["merge-base", "--is-ancestor", upstream_branch, "HEAD"],
        cwd=repo_root_path,
        label="git-rebase:merge-base",
        options=GitRunOptions(env=_git_env()),
    )
    if result.returncode == 0:
        return True
    if result.returncode == 1:
        return False
    raise RebaseVerificationError(
        f"git merge-base failed: {result.stderr.strip() or result.stdout.strip()}"
    )


def _close_repo(repo: Repo) -> None:
    close = cast("Callable[[], object] | None", getattr(repo, "close", None))
    if callable(close):
        close()


open_repo = _open_repo
rebase_in_progress_impl = _rebase_in_progress_impl
has_index_conflicts = _has_index_conflicts
head_is_descendant = _head_is_descendant


# ----- AC-14 catalog evidence -----
# This file is the authoritative source for the catalog entries listed
# below. Each ``# AC-14 rationale: <ID>`` line is the code-adjacent
# marker the AC-14 audit looks for; each ``# ladder rung: <N>``
# names the rung the entry sits on. Adding a new entry here requires
# BOTH lines or the audit fails.

# AC-14 rationale: G1
# ladder rung: 2
# ----- end AC-14 catalog evidence -----
