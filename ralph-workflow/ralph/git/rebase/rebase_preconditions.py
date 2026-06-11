"""Precondition validation before performing a git rebase."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from git import InvalidGitRepositoryError, Repo
from git.exc import GitCommandError

from ralph.git.rebase._concurrent_operation import _ConcurrentOperation
from ralph.git.subprocess_runner import run_git

if TYPE_CHECKING:
    from collections.abc import Sequence

    from git.config import GitConfigParser

REBASE_APPLY_DIR = "rebase-apply"
REBASE_MERGE_DIR = "rebase-merge"
_LOCK_FILES = ("index.lock", "packed-refs.lock", "HEAD.lock")


class RebasePreconditionError(Exception):
    """Raised when a rebase cannot start because a precondition failed."""


def check_rebase_preconditions(repo_root: Path | str) -> None:
    """Ensure the git repository is ready to start a rebase.

    Args:
        repo_root: Path to the git repository.

    Raises:
        RebasePreconditionError: When the repository is not ready to rebase.
    """
    repo = _open_repo(repo_root)
    try:
        _validate_git_state(repo)

        concurrent = _detect_concurrent_operation(repo)
        if concurrent:
            raise RebasePreconditionError(
                f"Cannot start rebase: {concurrent.description} already in progress. "
                "Please complete or abort the current operation first."
            )

        _ensure_git_identity(repo)
        _check_shallow_clone(repo)
        _ensure_clean_worktree(repo)
        _check_worktree_conflicts(repo)
        _check_submodule_state(repo)
        _check_sparse_checkout_state(repo)
    finally:
        repo.close()


def _open_repo(repo_root: Path | str) -> Repo:
    try:
        return Repo(repo_root)
    except InvalidGitRepositoryError as exc:
        raise RebasePreconditionError(f"Not a git repository: {exc}") from exc


def _validate_git_state(repo: Repo) -> None:
    try:
        head = repo.head
    except (GitCommandError, ValueError, TypeError) as exc:
        raise RebasePreconditionError(f"Repository HEAD is invalid: {exc}") from exc

    try:
        commit = head.commit
        _ = commit.tree
    except (GitCommandError, ValueError, OSError) as exc:
        raise RebasePreconditionError(f"Object database corruption: {exc}") from exc

    try:
        _ = repo.index
    except (GitCommandError, OSError) as exc:
        raise RebasePreconditionError(f"Repository index is corrupted: {exc}") from exc


def _detect_concurrent_operation(repo: Repo) -> _ConcurrentOperation | None:
    git_dir = _git_dir(repo)

    if (git_dir / REBASE_MERGE_DIR).exists() or (git_dir / REBASE_APPLY_DIR).exists():
        return _ConcurrentOperation("rebase", "rebase")

    checks: Sequence[tuple[str, str]] = (
        ("MERGE_HEAD", "merge"),
        ("CHERRY_PICK_HEAD", "cherry-pick"),
        ("REVERT_HEAD", "revert"),
    )

    for filename, label in checks:
        if (git_dir / filename).exists():
            return _ConcurrentOperation(label, label)

    bisect_files = ("BISECT_LOG", "BISECT_START", "BISECT_NAMES")
    if any((git_dir / path).exists() for path in bisect_files):
        return _ConcurrentOperation("bisect", "bisect")

    if any((git_dir / lock_file).exists() for lock_file in _LOCK_FILES):
        return _ConcurrentOperation("lock", "another Git process")

    for entry in git_dir.iterdir():
        name = entry.name
        if any(keyword in name for keyword in ("REBASE", "MERGE", "CHERRY")):
            return _ConcurrentOperation("unknown", f"unknown operation: {name}")

    return None


def _ensure_git_identity(repo: Repo) -> None:
    reader = repo.config_reader()
    username = _read_config_value(reader, "user", "name")
    email = _read_config_value(reader, "user", "email")

    if not username or not email:
        raise RebasePreconditionError(
            "Git identity is not configured. Please set user.name and user.email:\n  "
            'git config --global user.name "Your Name"\n  '
            'git config --global user.email "you@example.com"',
        )


def _ensure_clean_worktree(repo: Repo) -> None:
    worktree = _worktree(repo)
    try:
        result = run_git(("status", "--porcelain"), cwd=worktree, label="rebase-preflight-status")
        if result.returncode == 0 and result.stdout.splitlines():
            raise RebasePreconditionError(
                "Working tree is not clean. Please commit or stash changes before rebasing."
            )
        if result.returncode == 0:
            return
    except OSError:
        pass

    if repo.is_dirty(untracked_files=True, submodules=True):
        raise RebasePreconditionError(
            "Working tree is not clean. Please commit or stash changes before rebasing."
        )


def _check_shallow_clone(repo: Repo) -> None:
    shallow = _git_dir(repo) / "shallow"
    if shallow.exists():
        try:
            content = shallow.read_text()
        except OSError as exc:
            raise RebasePreconditionError(f"Failed to read shallow clone metadata: {exc}") from exc

        line_count = len(content.splitlines())
        raise RebasePreconditionError(
            f"Repository is a shallow clone with {line_count} commits. "
            "Rebasing may fail due to missing history. "
            "Consider running: git fetch --unshallow"
        )


def _check_worktree_conflicts(repo: Repo) -> None:
    if repo.head.is_detached:
        return

    try:
        branch_name = repo.active_branch.name
    except (TypeError, GitCommandError):
        return

    worktrees_dir = _git_dir(repo) / "worktrees"
    if not worktrees_dir.is_dir():
        return

    target_ref = f"refs/heads/{branch_name}"
    for entry in worktrees_dir.iterdir():
        if not entry.is_dir():
            continue
        head_file = entry / "HEAD"
        if not head_file.exists():
            continue

        try:
            content = head_file.read_text()
        except OSError:
            continue

        if target_ref in content:
            raise RebasePreconditionError(
                f"Branch '{branch_name}' is already checked out in worktree '{entry.name}'. "
                "Use 'git worktree add' to create a new worktree for this branch."
            )


def _check_submodule_state(repo: Repo) -> None:
    workdir = _worktree(repo)
    gitmodules = workdir / ".gitmodules"
    if not gitmodules.exists():
        return

    modules_dir = _git_dir(repo) / "modules"
    if not modules_dir.exists():
        raise RebasePreconditionError(
            "Submodules are not initialized. Run: git submodule update --init --recursive"
        )

    content = gitmodules.read_text()
    if "path =" not in content:
        return

    for line in content.splitlines():
        if "path =" not in line:
            continue
        _, _, remainder = line.partition("path =")
        path_value = remainder.strip()
        if not path_value:
            continue
        submodule_path = workdir / path_value
        if not submodule_path.exists():
            raise RebasePreconditionError(
                f"Submodule '{path_value}' is not initialized. Run: "
                "git submodule update --init --recursive"
            )


def _check_sparse_checkout_state(repo: Repo) -> None:
    reader = repo.config_reader()
    sparse_enabled = _config_value_as_bool(reader, "core", "sparseCheckout")
    cone_enabled = _config_value_as_bool(reader, "extensions", "sparseCheckoutCone")

    if not (sparse_enabled or cone_enabled):
        return

    info_sparse = _git_dir(repo) / "info" / "sparse-checkout"
    if not info_sparse.exists():
        raise RebasePreconditionError(
            "Sparse checkout is enabled but not configured. Run: git sparse-checkout init"
        )

    if not info_sparse.read_text().strip():
        raise RebasePreconditionError(
            "Sparse checkout configuration is empty. Run: git sparse-checkout set <patterns>"
        )


def _read_config_value(reader: GitConfigParser, section: str, key: str) -> str | None:
    try:
        value = reader.get_value(section, key)
    except Exception:
        return None
    if not value:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    stripped = str(value).strip()
    return stripped or None


def _config_value_as_bool(reader: GitConfigParser, section: str, key: str) -> bool:
    value = _read_config_value(reader, section, key)
    if value is None:
        return False
    return value.lower() in ("true", "1", "yes", "on")


def _git_dir(repo: Repo) -> Path:
    git_dir = repo.git_dir
    if not git_dir:
        raise RebasePreconditionError("Cannot determine .git directory for repository")
    return Path(git_dir)


def _worktree(repo: Repo) -> Path:
    if repo.working_tree_dir:
        return Path(repo.working_tree_dir)
    return _git_dir(repo).parent
