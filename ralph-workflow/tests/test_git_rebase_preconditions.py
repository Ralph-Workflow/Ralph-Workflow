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

pytestmark = [pytest.mark.subprocess_e2e, pytest.mark.timeout_seconds(10)]


def test_allows_clean_repository(tmp_git_repo: Path) -> None:

    check_rebase_preconditions(tmp_git_repo)


def test_tracked_modification_still_blocks_rebase_preconditions(
    tmp_git_repo: Path,
) -> None:

    (tmp_git_repo / "README.md").write_text("locally modified", encoding="utf-8")

    with pytest.raises(
        RebasePreconditionError,
        match="Working tree is not clean",
    ):
        check_rebase_preconditions(tmp_git_repo)


def test_untracked_file_does_not_block_rebase_preconditions(
    tmp_git_repo: Path,
) -> None:

    (tmp_git_repo / "scratch.txt").write_text("untracked\n", encoding="utf-8")

    check_rebase_preconditions(tmp_git_repo)


def test_untracked_directory_does_not_block_rebase_preconditions(
    tmp_git_repo: Path,
) -> None:

    build_dir = tmp_git_repo / "build"
    build_dir.mkdir(parents=True)
    (build_dir / "out.txt").write_text("untracked\n", encoding="utf-8")

    check_rebase_preconditions(tmp_git_repo)


def test_detects_missing_identity(tmp_git_repo: Path) -> None:

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

    (tmp_git_repo / ".git" / "rebase-apply").mkdir(exist_ok=True)

    with pytest.raises(
        RebasePreconditionError,
        match="rebase already in progress",
    ):
        check_rebase_preconditions(tmp_git_repo)


def test_detects_shallow_clone(tmp_git_repo: Path) -> None:

    shallow = tmp_git_repo / ".git" / "shallow"
    valid_commit = "0" * 40
    shallow.write_text(f"{valid_commit}\n{valid_commit}\n")

    with pytest.raises(
        RebasePreconditionError,
        match="shallow clone with 2 commits",
    ):
        check_rebase_preconditions(tmp_git_repo)


def _commit_gitmodules(repo_root: Path) -> None:
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
    with Repo(repo_root) as repo:
        repo.git.worktree("add", "-b", "wt-feature", str(worktree_path))


def test_uninitialized_submodule_does_not_block(tmp_git_repo: Path) -> None:

    _commit_gitmodules(tmp_git_repo)

    check_rebase_preconditions(tmp_git_repo)


def test_linked_worktree_passes_preconditions(tmp_git_repo: Path, tmp_path: Path) -> None:

    _commit_gitmodules(tmp_git_repo)
    worktree_path = tmp_path / "linked-worktree"
    _add_linked_worktree(tmp_git_repo, worktree_path)

    check_rebase_preconditions(worktree_path)


def test_detects_shallow_clone_from_linked_worktree(tmp_git_repo: Path, tmp_path: Path) -> None:

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

    (tmp_git_repo / ".git" / "AUTO_MERGE").write_text("")

    check_rebase_preconditions(tmp_git_repo)


@pytest.mark.parametrize(
    "leftover",
    ["MERGE_MSG", "MERGE_MODE", "MERGE_RR", "SQUASH_MSG", "ORIG_HEAD"],
)
def test_stale_merge_bookkeeping_files_do_not_block(tmp_git_repo: Path, leftover: str) -> None:

    marker = tmp_git_repo / ".git" / leftover
    marker.write_text("")
    try:
        check_rebase_preconditions(tmp_git_repo)
    finally:
        marker.unlink()


def test_real_merge_head_still_blocks(tmp_git_repo: Path) -> None:

    (tmp_git_repo / ".git" / "MERGE_HEAD").write_text("0" * 40)

    with pytest.raises(
        RebasePreconditionError,
        match="merge already in progress",
    ):
        check_rebase_preconditions(tmp_git_repo)


def test_real_cherry_pick_and_revert_still_block(tmp_git_repo: Path) -> None:

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
