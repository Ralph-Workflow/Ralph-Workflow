"""Git operations for ralph pipeline via GitPython.

This module provides a high-level interface for git operations,
wrapping GitPython to provide the functionality needed by the pipeline.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, cast

from git import Actor, InvalidGitRepositoryError, Repo
from loguru import logger

if TYPE_CHECKING:
    from collections.abc import Callable


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
    repo: Repo | None = None
    try:
        repo = Repo(repo_root)
        return not repo.is_dirty()
    finally:
        _close_repo(repo)


def has_uncommitted_changes(repo_root: Path | str) -> bool:
    """Return True when the working tree has uncommitted work.

    Includes staged diff, unstaged diff, and untracked files. This is the
    authoritative skip check for commit phases: if this returns False, there
    is literally nothing for a commit agent to package up.
    """
    repo: Repo | None = None
    try:
        repo = Repo(repo_root)
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
    repo: Repo | None = None
    try:
        repo = Repo(repo_root)
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
    repo: Repo | None = None
    try:
        repo = Repo(repo_root)
        return bool(repo.index.diff("HEAD")) or bool(repo.untracked_files)
    finally:
        _close_repo(repo)


def get_staged_files(repo_root: Path | str) -> list[str]:
    """Get list of staged files.

    Args:
        repo_root: Path to the repository root.

    Returns:
        List of staged file paths.
    """
    repo: Repo | None = None
    try:
        repo = Repo(repo_root)
        staged = repo.index.diff("HEAD")
        return [item.a_path for item in staged if item.a_path] if staged else []
    finally:
        _close_repo(repo)


def stage_all(repo_root: Path | str) -> None:
    """Stage all changes (git add -A).

    Args:
        repo_root: Path to the repository root.
    """
    repo: Repo | None = None
    try:
        repo = Repo(repo_root)
        repo.git.add(A=True)
        logger.debug("Staged all changes in {}", repo_root)
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
        repo.git.add("--all", "--", *files)
        logger.debug("Staged {} selected paths in {}", len(files), repo_root)
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

        if not author_name or not author_email:
            try:
                config = repo.config_reader()
                author_name = author_name or str(config.get_value("user", "name", "Ralph"))
                author_email = author_email or str(config.get_value("user", "email", "ralph@ai"))
            except Exception:
                author_name = author_name or "Ralph"
                author_email = author_email or "ralph@ai"

        actor = Actor(author_name, author_email)
        commit = repo.index.commit(message, author=actor, committer=actor)
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
