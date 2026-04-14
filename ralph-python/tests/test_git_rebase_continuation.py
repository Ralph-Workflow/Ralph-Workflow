"""Tests for the rebase continuation helpers."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from git import Repo

from ralph.git.rebase.rebase_continuation import (
    ConflictRemainingError,
    NoRebaseInProgressError,
    continue_rebase_at,
    rebase_in_progress_at,
    verify_rebase_completed_at,
)


def test_continue_rebase_finishes_conflicted_rebase(tmp_git_repo: Path) -> None:
    """Continuing after resolving conflicts completes the rebase."""

    base_branch = _setup_conflicted_rebase(tmp_git_repo)
    assert rebase_in_progress_at(tmp_git_repo)
    assert not verify_rebase_completed_at(tmp_git_repo, base_branch)

    _resolve_conflict(tmp_git_repo)
    continue_rebase_at(tmp_git_repo)

    assert not rebase_in_progress_at(tmp_git_repo)
    assert verify_rebase_completed_at(tmp_git_repo, base_branch)


def test_continue_rebase_requires_clean_index(tmp_git_repo: Path) -> None:
    """The continuation helper refuses to run until conflicts are resolved."""

    _setup_conflicted_rebase(tmp_git_repo)

    with pytest.raises(ConflictRemainingError):
        continue_rebase_at(tmp_git_repo)

    assert rebase_in_progress_at(tmp_git_repo)


def test_continue_rebase_requires_active_rebase(tmp_git_repo: Path) -> None:
    """Continuing without a rebase in progress is an error."""

    with pytest.raises(NoRebaseInProgressError):
        continue_rebase_at(tmp_git_repo)


def _setup_conflicted_rebase(repo_root: Path, feature_branch: str = "feature") -> str:
    repo = Repo(repo_root)
    base_branch = repo.active_branch.name
    conflict_file = repo_root / "conflict.txt"

    conflict_file.write_text("base\n")
    repo.index.add(["conflict.txt"])
    repo.index.commit("add conflict file")

    repo.git.checkout("-b", feature_branch)
    conflict_file.write_text("feature\n")
    repo.index.add(["conflict.txt"])
    repo.index.commit("feature change")

    repo.git.checkout(base_branch)
    conflict_file.write_text("main\n")
    repo.index.add(["conflict.txt"])
    repo.index.commit("main change")

    repo.git.checkout(feature_branch)
    result = subprocess.run(
        ["git", "rebase", base_branch],
        cwd=str(repo_root),
        text=True,
        capture_output=True,
    )
    assert result.returncode != 0, "Expected the rebase command to conflict"

    return base_branch


def _resolve_conflict(repo_root: Path) -> None:
    conflict_file = repo_root / "conflict.txt"
    conflict_file.write_text("resolved\n")
    subprocess.run(
        ["git", "add", "conflict.txt"],
        cwd=str(repo_root),
        check=True,
        capture_output=True,
        text=True,
    )
