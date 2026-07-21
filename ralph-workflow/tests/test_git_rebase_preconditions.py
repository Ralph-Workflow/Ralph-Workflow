"""Tests for git rebase precondition validation."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from git import Repo

from ralph.git.rebase import (
    RebasePreconditionError,
    check_rebase_preconditions,
)

if TYPE_CHECKING:
    from pathlib import Path

# All tests in this module exercise real git operations against the
# ``tmp_git_repo`` fixture (per-test process-isolated git repository).
# Wall-clock cost under parallel xdist load is regularly > 1 s on busy
# machines, so the default 1-second per-test ceiling is unsafe.
pytestmark = pytest.mark.timeout_seconds(5)


def test_allows_clean_repository(tmp_git_repo: Path) -> None:
    """A clean repository should pass all precondition checks."""

    check_rebase_preconditions(tmp_git_repo)


def test_detects_dirty_worktree(tmp_git_repo: Path) -> None:
    """Dirty worktrees should trigger a precondition error."""

    (tmp_git_repo / "dirty.txt").write_text("uncommitted")

    with pytest.raises(
        RebasePreconditionError,
        match="Working tree is not clean",
    ):
        check_rebase_preconditions(tmp_git_repo)


def test_detects_missing_identity(tmp_git_repo: Path) -> None:
    """Missing git user configuration should be reported."""

    with Repo(tmp_git_repo) as repo:
        writer = repo.config_writer()
        writer.remove_section("user")
        writer.set_value("user", "name", "")
        writer.set_value("user", "email", "")
        writer.release()

    with pytest.raises(
        RebasePreconditionError,
        match="Git identity is not configured",
    ):
        check_rebase_preconditions(tmp_git_repo)


def test_detects_concurrent_rebase(tmp_git_repo: Path) -> None:
    """Detect when another rebase-like state blocks the operation."""

    (tmp_git_repo / ".git" / "rebase-apply").mkdir(exist_ok=True)

    with pytest.raises(
        RebasePreconditionError,
        match="rebase already in progress",
    ):
        check_rebase_preconditions(tmp_git_repo)


def test_detects_shallow_clone(tmp_git_repo: Path) -> None:
    """Shallow clones should fail precondition validation."""

    shallow = tmp_git_repo / ".git" / "shallow"
    valid_commit = "0" * 40
    shallow.write_text(f"{valid_commit}\n{valid_commit}\n")

    with pytest.raises(
        RebasePreconditionError,
        match="shallow clone with 2 commits",
    ):
        check_rebase_preconditions(tmp_git_repo)


def _commit_gitmodules(repo_root: Path) -> None:
    """Commit a .gitmodules declaring a never-initialized submodule."""
    (repo_root / ".gitmodules").write_text(
        '[submodule "vendor/brand"]\n'
        "\tpath = vendor/brand\n"
        "\turl = https://example.invalid/brand.git\n"
    )
    (repo_root / "vendor" / "brand").mkdir(parents=True)
    with Repo(repo_root) as repo:
        repo.index.add([".gitmodules"])
        repo.index.commit("declare submodule")


def _add_linked_worktree(repo_root: Path, worktree_path: Path) -> None:
    """Create a linked worktree on a fresh branch."""
    with Repo(repo_root) as repo:
        repo.git.worktree("add", "-b", "wt-feature", str(worktree_path))


def test_uninitialized_submodule_does_not_block(tmp_git_repo: Path) -> None:
    """A declared-but-uninitialized submodule must never block a rebase."""

    _commit_gitmodules(tmp_git_repo)

    check_rebase_preconditions(tmp_git_repo)


def test_linked_worktree_passes_preconditions(
    tmp_git_repo: Path, tmp_path: Path
) -> None:
    """A clean linked worktree passes, even with an uninitialized submodule."""

    _commit_gitmodules(tmp_git_repo)
    worktree_path = tmp_path / "linked-worktree"
    _add_linked_worktree(tmp_git_repo, worktree_path)

    check_rebase_preconditions(worktree_path)


def test_detects_shallow_clone_from_linked_worktree(
    tmp_git_repo: Path, tmp_path: Path
) -> None:
    """The shallow marker lives in the common git dir; worktrees must see it."""

    worktree_path = tmp_path / "linked-worktree"
    _add_linked_worktree(tmp_git_repo, worktree_path)
    valid_commit = "0" * 40
    (tmp_git_repo / ".git" / "shallow").write_text(f"{valid_commit}\n")

    with pytest.raises(
        RebasePreconditionError,
        match="shallow clone with 1 commits",
    ):
        check_rebase_preconditions(worktree_path)


def test_stale_auto_merge_marker_does_not_block(tmp_git_repo: Path) -> None:
    """A leftover ``AUTO_MERGE`` must never read as an in-progress merge.

    Git's ``ort`` merge strategy writes ``AUTO_MERGE`` into the git dir
    whenever a merge stops on conflicts, and neither ``merge --abort``
    nor the follow-up merge commit removes it. Treating that leftover
    as a concurrent operation permanently disabled auto-integration in
    every worktree that had ever hit a single conflict.
    """

    (tmp_git_repo / ".git" / "AUTO_MERGE").write_text("")

    check_rebase_preconditions(tmp_git_repo)


@pytest.mark.parametrize(
    "leftover",
    ["MERGE_MSG", "MERGE_MODE", "MERGE_RR", "SQUASH_MSG", "ORIG_HEAD"],
)
def test_stale_merge_bookkeeping_files_do_not_block(
    tmp_git_repo: Path, leftover: str
) -> None:
    """Bookkeeping git leaves behind after an operation must not block."""

    marker = tmp_git_repo / ".git" / leftover
    marker.write_text("")
    try:
        check_rebase_preconditions(tmp_git_repo)
    finally:
        marker.unlink()


def test_real_merge_head_still_blocks(tmp_git_repo: Path) -> None:
    """``MERGE_HEAD`` proves a genuine in-progress merge and must block."""

    (tmp_git_repo / ".git" / "MERGE_HEAD").write_text("0" * 40)

    with pytest.raises(
        RebasePreconditionError,
        match="merge already in progress",
    ):
        check_rebase_preconditions(tmp_git_repo)


def test_real_cherry_pick_and_revert_still_block(tmp_git_repo: Path) -> None:
    """``CHERRY_PICK_HEAD`` and ``REVERT_HEAD`` must keep blocking."""

    for filename, description in (
        ("CHERRY_PICK_HEAD", "cherry-pick already in progress"),
        ("REVERT_HEAD", "revert already in progress"),
    ):
        marker = tmp_git_repo / ".git" / filename
        marker.write_text("0" * 40)
        try:
            with pytest.raises(RebasePreconditionError, match=description):
                check_rebase_preconditions(tmp_git_repo)
        finally:
            marker.unlink()
