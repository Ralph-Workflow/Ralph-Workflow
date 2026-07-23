"""Precondition validation before performing a git rebase."""

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
    :func:`ralph.pipeline.auto_integrate_rebase_merge.run_rebase_or_merge`
    already
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

    # ONE source of truth for "who has this branch checked out":
    # ``git worktree list --porcelain``, via the same ``worktree_lookup``
    # the fast-forward side already trusts. The previous implementation
    # walked ``<common_dir>/worktrees/*/HEAD``, which has no entry for the
    # PRIMARY worktree -- so a branch checked out in the primary checkout
    # was invisible here while being plainly visible to the landing path.
    # Two answers to one question is a divergence waiting to strand a
    # rebase; asking git directly removes it.
    if not _has_linked_worktrees(repo):
        # Provably equivalent fast path: with no LINKED worktrees the only
        # checkout in the repository is this one, so no other worktree can
        # be holding the branch. Worth special-casing because the general
        # answer costs a git subprocess and single-worktree repositories
        # are the overwhelmingly common case -- paying ~40 ms on every
        # integration to re-learn "there is only one checkout" is real
        # time against a hard test budget.
        return

    repo_root = _repo_root_for_worktree_query(repo)
    verdict, holder = worktree_lookup(repo_root, branch_name)
    if verdict == WORKTREE_QUERY_FAILED:
        # Fail CLOSED, matching ``_TARGET_WORKTREE_QUERY_FAILED`` on the
        # fast-forward side: an unanswerable query is not evidence that
        # nobody holds the branch, and rebasing a branch another checkout
        # is sitting on corrupts that checkout's index.
        raise RebasePreconditionError(
            f"Could not determine whether branch '{branch_name}' is checked "
            "out in another worktree."
        )
    if verdict != WORKTREE_FOUND or holder is None:
        return
    if _is_same_worktree(holder, repo_root):
        # The branch being rebased is by construction checked out HERE.
        # Without this self-skip every rebase would fail its own
        # precondition.
        return
    raise RebasePreconditionError(
        f"Branch '{branch_name}' is already checked out in worktree '{holder.name}'. "
        "Use 'git worktree add' to create a new worktree for this branch."
    )


def _has_linked_worktrees(repo: Repo) -> bool:
    """Whether the repository has any worktree beyond the primary one.

    Pure filesystem check against the registration directory in the
    common git dir. Unlike the scan this replaced, the directory's
    CONTENTS are never interpreted -- only its emptiness -- so the fact
    that it has no entry for the primary worktree cannot cause a wrong
    answer here: an empty directory means the primary is the only
    checkout, which is exactly the conclusion drawn.
    """
    worktrees_dir = _common_git_dir(repo) / "worktrees"
    try:
        return worktrees_dir.is_dir() and any(worktrees_dir.iterdir())
    except OSError:
        # Unreadable: fall through to the authoritative git query rather
        # than assuming a single checkout.
        return True


def _repo_root_for_worktree_query(repo: Repo) -> Path:
    """Working-tree root to run ``git worktree list`` from."""
    if repo.working_tree_dir:
        return Path(repo.working_tree_dir)
    return _git_dir(repo).parent


def _is_same_worktree(candidate: Path, repo_root: Path) -> bool:
    """Whether two worktree paths denote the same checkout.

    Resolved before comparing so a symlinked or ``/private``-prefixed
    temporary directory -- the normal shape of a macOS test fixture --
    does not read as a second worktree holding the branch.
    """
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
    """Resolve the git directory shared by every worktree of the repository.

    ``repo.git_dir`` for a linked worktree is its private
    ``.git/worktrees/<name>`` directory; state shared across worktrees
    (the ``shallow`` marker, the ``worktrees/`` registry) lives only in
    the common directory. For a primary checkout both are the same path.
    """
    common: object = getattr(repo, "common_dir", None)
    if isinstance(common, str) and common:
        return Path(common)
    return _git_dir(repo)


def _worktree(repo: Repo) -> Path:
    if repo.working_tree_dir:
        return Path(repo.working_tree_dir)
    return _git_dir(repo).parent


# ----- AC-14 catalog evidence -----
# This file is the authoritative source for the catalog entries listed
# below. Each ``# AC-14 rationale: <ID>`` line is the code-adjacent
# marker the AC-14 audit looks for; each ``# ladder rung: <N>``
# names the rung the entry sits on. Adding a new entry here requires
# BOTH lines or the audit fails.

# AC-14 rationale: A7
# ladder rung: 4
# AC-14 rationale: A8
# ladder rung: 1
# AC-14 rationale: D10
# ladder rung: 1
# AC-14 rationale: D7
# ladder rung: 4
# AC-14 rationale: H1
# ladder rung: 4
# AC-14 rationale: H2
# ladder rung: 4
# AC-14 rationale: H3
# ladder rung: 4
# AC-14 rationale: H5
# ladder rung: 4
# ----- end AC-14 catalog evidence -----
