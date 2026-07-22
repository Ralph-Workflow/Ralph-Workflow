"""Precondition validation before performing a git rebase."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from git import InvalidGitRepositoryError, Repo
from git.exc import GitCommandError
from loguru import logger

from ralph.git.rebase._concurrent_operation import _ConcurrentOperation
from ralph.git.subprocess_runner import run_git

if TYPE_CHECKING:
    from git.config import GitConfigParser

REBASE_APPLY_DIR = "rebase-apply"
REBASE_MERGE_DIR = "rebase-merge"
_LOCK_FILES = ("index.lock", "packed-refs.lock", "HEAD.lock")

#: Files whose presence proves a real in-progress operation. Closed set:
#: git may add new bookkeeping filenames at any time, so an unknown
#: entry must never be treated as a blocking operation.
_IN_PROGRESS_MARKER_FILES: tuple[tuple[str, str], ...] = (
    ("MERGE_HEAD", "merge"),
    ("CHERRY_PICK_HEAD", "cherry-pick"),
    ("REVERT_HEAD", "revert"),
    ("REBASE_HEAD", "rebase"),
)

#: Bookkeeping files git leaves behind AFTER an operation finishes or is
#: aborted. ``AUTO_MERGE`` is written by the ``ort`` strategy on every
#: conflicted merge and survives ``merge --abort``; treating it as an
#: in-progress operation permanently disabled auto-integration in any
#: worktree that had ever hit a conflict.
_BENIGN_LEFTOVER_ENTRIES: frozenset[str] = frozenset(
    {"AUTO_MERGE", "MERGE_MSG", "MERGE_MODE", "MERGE_RR", "SQUASH_MSG", "ORIG_HEAD"}
)


class RebasePreconditionError(Exception):
    """Raised when a rebase cannot start because a precondition failed."""


def check_rebase_preconditions(repo_root: Path | str) -> None:
    """Ensure the git repository is ready to start a rebase.

    Only a closed set of authoritative markers blocks a rebase: the
    ``rebase-merge``/``rebase-apply`` directories, the in-progress
    marker files in :data:`_IN_PROGRESS_MARKER_FILES`, the bisect
    state files and the git lock files. Any other git-dir entry --
    including bookkeeping git leaves behind after an operation ends,
    such as ``AUTO_MERGE`` -- is observed at DEBUG level but never
    blocks.

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
    """Block only on uncommitted TRACKED modifications.

    Untracked files and submodule-pointer drift are deliberately
    tolerated, because ``git rebase`` and ``git merge`` tolerate them
    too: git refuses non-destructively, and only for the specific
    untracked path that would actually be overwritten. That refusal
    surfaces as ``RebaseFailed``, which
    :func:`ralph.pipeline.auto_integrate._run_rebase_or_merge` already
    routes into the endpoint-merge fallback. Blocking up front instead
    turned a per-file, git-detectable hazard into a run-wide outage:
    one scratch file left by a phase disabled integration for every
    later commit seam.

    ``ralph.git.operations.is_repo_clean`` is the existing precedent
    for the ``--untracked-files=no`` definition of "clean".
    """
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
    # The shallow marker is shared repository state: it lives in the
    # common git dir, never in a linked worktree's private git dir.
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

    # Worktree registrations live in the common git dir; when running
    # from a linked worktree the repo's own registration must be
    # skipped or the current branch would always self-conflict.
    worktrees_dir = _common_git_dir(repo) / "worktrees"
    if not worktrees_dir.is_dir():
        return

    own_git_dir = _git_dir(repo).resolve()
    target_ref = f"refs/heads/{branch_name}"
    for entry in worktrees_dir.iterdir():
        if not entry.is_dir():
            continue
        if entry.resolve() == own_git_dir:
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
    """Resolve the git directory shared by every worktree of the repository.

    ``repo.git_dir`` for a linked worktree is its private
    ``.git/worktrees/<name>`` directory; state shared across worktrees
    (the ``shallow`` marker, the ``worktrees/`` registry) lives only in
    the common directory. For a primary checkout both are the same path.
    """
    common: object = getattr(repo, "common_dir", None)
    if isinstance(common, str) and common:
        return Path(common).resolve()
    return _git_dir(repo).resolve()


def _worktree(repo: Repo) -> Path:
    if repo.working_tree_dir:
        return Path(repo.working_tree_dir)
    return _git_dir(repo).parent
