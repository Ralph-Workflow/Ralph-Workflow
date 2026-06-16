"""Git operations for ralph pipeline via GitPython.

This module provides a high-level interface for git operations,
wrapping GitPython to provide the functionality needed by the pipeline.
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import TYPE_CHECKING, cast

from git import Actor, InvalidGitRepositoryError, Repo
from git.exc import GitCommandError
from loguru import logger

from ralph.git.subprocess_runner import run_git

if TYPE_CHECKING:
    from collections.abc import Callable

_LOCK_PATH_PATTERN = re.compile(r"Unable to create '([^']+\.lock)'")
_RECOVERABLE_GIT_LOCK_FILES = frozenset({"index.lock", "HEAD.lock", "packed-refs.lock"})
_STALE_GIT_LOCK_AGE_SECONDS = 10.0
_PORCELAIN_STATUS_PREFIX_LEN = 3
_RALPH_WORKFLOW_COAUTHOR_TRAILER = "Co-authored-by: Ralph Workflow <noreply@ralphworkflow.com>"


class GitOperationError(Exception):
    """Raised when a git operation fails.

    Attributes:
        operation: Name of the operation that failed.
        message: Error message describing the failure.
    """

    def __init__(self, operation: str, message: str) -> None:
        """Initialize git operation error.

        Args:
            operation: Name of the operation.
            message: Error message.
        """
        self.operation = operation
        self.message = message
        super().__init__(f"Git {operation} failed: {message}")


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


def _recover_stale_git_lock(
    operation: str,
    error: Exception,
    *,
    stale_lock_age_seconds: float = _STALE_GIT_LOCK_AGE_SECONDS,
) -> bool:
    error_text = str(error)
    lock_path = _parse_lock_path_from_error(error_text)
    if lock_path is None:
        return False
    if lock_path.exists():
        age_seconds = time.time() - lock_path.stat().st_mtime
        if age_seconds < stale_lock_age_seconds:
            return False
        try:
            lock_path.unlink()
        except OSError:
            return False
        logger.warning(
            "Recovered stale git lock for {} by removing {} (age={:.1f}s)",
            operation,
            lock_path,
            age_seconds,
        )
        return True
    logger.warning(
        "Retrying {} after transient git lock contention; lock path already disappeared: {}",
        operation,
        lock_path,
    )
    return True


def _run_git_operation_with_stale_lock_recovery[T](
    operation: str,
    action: Callable[[], T],
) -> T:
    try:
        return action()
    except GitCommandError as exc:
        if not _recover_stale_git_lock(operation, exc):
            raise
        return action()


def _append_ralph_workflow_coauthor_trailer(message: str) -> str:
    stripped_message = message.rstrip()
    if not stripped_message:
        return message
    lowered_lines = {line.strip().lower() for line in stripped_message.splitlines()}
    if _RALPH_WORKFLOW_COAUTHOR_TRAILER.lower() in lowered_lines:
        return stripped_message
    return f"{stripped_message}\n\n{_RALPH_WORKFLOW_COAUTHOR_TRAILER}"


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
            ("status", "--porcelain", "--untracked-files=no"),
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
        True if there are staged changes.
    """
    try:
        status_lines = _git_status_porcelain_lines(Path(repo_root))
    except OSError:
        repo: Repo | None = None
        try:
            repo = Repo(repo_root)
            return bool(repo.index.diff("HEAD")) or bool(repo.untracked_files)
        finally:
            _close_repo(repo)
    return any(
        line.startswith("??") or (line and line[0] not in {" ", "?"}) for line in status_lines
    )


def list_changed_paths(repo_root: Path | str) -> list[str]:
    """Return unique changed paths from ``git status --porcelain`` in output order."""
    try:
        status_lines = _git_status_porcelain_lines(Path(repo_root))
    except OSError:
        repo: Repo | None = None
        try:
            repo = Repo(repo_root)
            status_lines = cast("str", repo.git.status("--porcelain")).splitlines()
        finally:
            _close_repo(repo)

    changed_paths: list[str] = []
    for line in status_lines:
        if not line or len(line) <= _PORCELAIN_STATUS_PREFIX_LEN:
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
        List of staged file paths.
    """
    try:
        status_lines = _git_status_porcelain_lines(Path(repo_root))
    except OSError:
        repo: Repo | None = None
        try:
            repo = Repo(repo_root)
            staged = repo.index.diff("HEAD")
            return [item.a_path for item in staged if item.a_path] if staged else []
        finally:
            _close_repo(repo)

    staged_paths: list[str] = []
    for line in status_lines:
        if not line or line[0] in {" ", "?"} or len(line) <= _PORCELAIN_STATUS_PREFIX_LEN:
            continue
        path_part = line[_PORCELAIN_STATUS_PREFIX_LEN:]
        if " -> " in path_part:
            _, _, path_part = path_part.partition(" -> ")
        path = path_part.strip()
        if path and path not in staged_paths:
            staged_paths.append(path)
    return staged_paths


def _git_status_porcelain_lines(repo_root: Path) -> list[str]:
    result = run_git(("status", "--porcelain"), cwd=repo_root, label="git-status")
    if result.returncode != 0:
        return []
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
            _ = cast("str", repo.git.add(A=True))

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
            _ = cast("str", repo.git.add("--all", "--", *files))

        _run_git_operation_with_stale_lock_recovery("stage_files", _stage)
        logger.debug("Staged {} selected paths in {}", len(files), repo_root)
    except Exception as exc:
        raise GitOperationError("stage_files", str(exc)) from exc
    finally:
        _close_repo(repo)


def create_commit(
    repo_root: Path | str,
    message: str,
    author_name: str | None = None,
    author_email: str | None = None,
) -> str:
    """Create a git commit.

    Args:
        repo_root: Path to the repository root.
        message: Commit message.
        author_name: Optional author name override.
        author_email: Optional author email override.

    Returns:
        SHA of the created commit.

    Raises:
        GitOperationError: If commit fails.
    """
    repo: Repo | None = None
    try:
        repo = Repo(repo_root)
        message = _append_ralph_workflow_coauthor_trailer(message)

        if not author_name or not author_email:
            try:
                config = repo.config_reader()
                author_name = author_name or str(config.get_value("user", "name", "Ralph"))
                author_email = author_email or str(config.get_value("user", "email", "ralph@ai"))
            except Exception:
                author_name = author_name or "Ralph"
                author_email = author_email or "ralph@ai"

        actor = Actor(author_name, author_email)
        commit = _run_git_operation_with_stale_lock_recovery(
            "create_commit",
            lambda: repo.index.commit(message, author=actor, committer=actor),
        )
        logger.info(
            "Created commit {}: {}",
            commit.hexsha[:8],
            message.splitlines()[0] if message else "(no message)",
        )
        return commit.hexsha
    except Exception as exc:
        raise GitOperationError("create_commit", str(exc)) from exc
    finally:
        _close_repo(repo)


def push(
    repo_root: Path | str,
    remote: str = "origin",
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
    """
    repo: Repo | None = None
    try:
        repo = Repo(repo_root)
        return repo.head.commit.hexsha
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
        with gitignore_path.open("a", encoding="utf-8") as f:
            if new_patterns[0]:
                f.write("\n")
            f.write("\n".join(new_patterns))
        logger.debug("Appended {} patterns to .gitignore", len(new_patterns))
