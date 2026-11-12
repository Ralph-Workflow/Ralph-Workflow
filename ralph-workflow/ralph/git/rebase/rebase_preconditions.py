from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from git import InvalidGitRepositoryError, Repo
from git.exc import GitCommandError
from loguru import logger

from ralph.git.merge import (
    WORKTREE_FOUND,
    WORKTREE_QUERY_FAILED,
    worktree_lookup,
)
from ralph.git.rebase._concurrent_operation import _ConcurrentOperation
from ralph.git.subprocess_runner import run_git

if TYPE_CHECKING:
    from git.config import GitConfigParser

REBASE_APPLY_DIR = "rebase-apply"
REBASE_MERGE_DIR = "rebase-merge"
_LOCK_FILES = ("index.lock", "packed-refs.lock", "HEAD.lock")

_IN_PROGRESS_MARKER_FILES: tuple[tuple[str, str], ...] = (
    ("MERGE_HEAD", "merge"),
    ("CHERRY_PICK_HEAD", "cherry-pick"),
    ("REVERT_HEAD", "revert"),
    ("REBASE_HEAD", "rebase"),
)

_BENIGN_LEFTOVER_ENTRIES: frozenset[str] = frozenset(
    {"AUTO_MERGE", "MERGE_MSG", "MERGE_MODE", "MERGE_RR", "SQUASH_MSG", "ORIG_HEAD"}
)


class RebasePreconditionError(Exception):
    pass


def check_rebase_preconditions(repo_root: Path | str) -> None:
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

    for filename, label in _IN_PROGRESS_MARKER_FILES:
        if (git_dir / filename).exists():
            return _ConcurrentOperation(label, label)

    bisect_files = ("BISECT_LOG", "BISECT_START", "BISECT_NAMES")
    if any((git_dir / path).exists() for path in bisect_files):
        return _ConcurrentOperation("bisect", "bisect")

    if any((git_dir / lock_file).exists() for lock_file in _LOCK_FILES):
        return _ConcurrentOperation("lock", "another Git process")

    unknown = sorted(
        entry.name
        for entry in git_dir.iterdir()
        if any(k in entry.name for k in ("REBASE", "MERGE", "CHERRY"))
        and entry.name not in _BENIGN_LEFTOVER_ENTRIES
    )
    if unknown:
        logger.debug(
            "rebase preconditions: ignoring unrecognized git-dir entries {}",
            unknown,
        )

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
        result = run_git(
            ("status", "--porcelain", "--untracked-files=no"),
            cwd=worktree,
            label="rebase-preflight-status",
        )
        if result.returncode == 0 and result.stdout.splitlines():
            raise RebasePreconditionError(
                "Working tree is not clean. Please commit or stash changes before rebasing."
            )
        if result.returncode == 0:
            return
    except OSError:
        pass

    if repo.is_dirty(untracked_files=False, submodules=False):
        raise RebasePreconditionError(
            "Working tree is not clean. Please commit or stash changes before rebasing."
        )


def _check_shallow_clone(repo: Repo) -> None:
    shallow = _common_git_dir(repo) / "shallow"
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

    if not _has_linked_worktrees(repo):
        return

    repo_root = _repo_root_for_worktree_query(repo)
    verdict, holder = worktree_lookup(repo_root, branch_name)
    if verdict == WORKTREE_QUERY_FAILED:
        raise RebasePreconditionError(
            f"Could not determine whether branch '{branch_name}' is checked "
            "out in another worktree."
        )
    if verdict != WORKTREE_FOUND or holder is None:
        return
    if _is_same_worktree(holder, repo_root):
        return
    raise RebasePreconditionError(
        f"Branch '{branch_name}' is already checked out in worktree '{holder.name}'. "
        "Use 'git worktree add' to create a new worktree for this branch."
    )


def _has_linked_worktrees(repo: Repo) -> bool:
    worktrees_dir = _common_git_dir(repo) / "worktrees"
    try:
        return worktrees_dir.is_dir() and any(worktrees_dir.iterdir())
    except OSError:
        return True


def _repo_root_for_worktree_query(repo: Repo) -> Path:
    if repo.working_tree_dir:
        return Path(repo.working_tree_dir)
    return _git_dir(repo).parent


def _is_same_worktree(candidate: Path, repo_root: Path) -> bool:
    try:
        return candidate.resolve() == repo_root.resolve()
    except OSError:
        return candidate == repo_root


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


def _common_git_dir(repo: Repo) -> Path:
    common: object = getattr(repo, "common_dir", None)
    if isinstance(common, str) and common:
        return Path(common)
    return _git_dir(repo)


def _worktree(repo: Repo) -> Path:
    if repo.working_tree_dir:
        return Path(repo.working_tree_dir)
    return _git_dir(repo).parent
