"""Tests for git rebase precondition validation."""

from __future__ import annotations

from pathlib import Path

import pytest
from git import Repo

from ralph.git.rebase import (
    RebasePreconditionError,
    check_rebase_preconditions,
)


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

    repo = Repo(tmp_git_repo)
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
