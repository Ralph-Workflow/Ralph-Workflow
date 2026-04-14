"""Unit tests for git operations."""

from __future__ import annotations

from pathlib import Path

import pytest
from git import Repo

from ralph.git.operations import (
    GitOperationError,
    append_to_gitignore,
    create_commit,
    find_repo_root,
    get_current_branch,
    get_head_sha,
    has_staged_changes,
    is_repo_clean,
    merge_base,
    push,
    stage_all,
)

FULL_SHA_LENGTH = 40
INITIAL_OCCURRENCE_COUNT = 1
DEFAULT_BRANCHES = {"main", "master"}


def test_find_repo_root(tmp_git_repo: Path) -> None:
    """Test finding repository root."""
    root = find_repo_root(tmp_git_repo)
    assert root == tmp_git_repo


def test_find_repo_root_not_git() -> None:
    """Test finding repo root when not in git repository."""
    with pytest.raises(GitOperationError, match="Not inside a git repository"):
        find_repo_root(Path("/tmp"))


def test_is_repo_clean(tmp_git_repo: Path) -> None:
    """Test checking if repository is clean."""
    assert is_repo_clean(tmp_git_repo) is True

    # Make a change
    readme = tmp_git_repo / "README.md"
    readme.write_text("updated content")

    assert is_repo_clean(tmp_git_repo) is False


def test_has_staged_changes(tmp_git_repo: Path) -> None:
    """Test checking for staged changes."""
    assert has_staged_changes(tmp_git_repo) is False

    # Stage a change
    readme = tmp_git_repo / "README.md"
    readme.write_text("updated content")
    stage_all(tmp_git_repo)

    assert has_staged_changes(tmp_git_repo) is True


def test_stage_all(tmp_git_repo: Path) -> None:
    """Test staging all changes."""
    readme = tmp_git_repo / "README.md"
    readme.write_text("updated content")

    stage_all(tmp_git_repo)

    repo = Repo(tmp_git_repo)
    staged = repo.index.diff("HEAD")
    assert len(staged) > 0


def test_create_commit(tmp_git_repo: Path) -> None:
    """Test creating a commit."""
    readme = tmp_git_repo / "README.md"
    readme.write_text("new content")
    stage_all(tmp_git_repo)

    sha = create_commit(tmp_git_repo, "Test commit message")

    assert len(sha) == FULL_SHA_LENGTH
    assert is_repo_clean(tmp_git_repo)


def test_create_commit_with_author(tmp_git_repo: Path) -> None:
    """Test creating a commit with custom author."""
    readme = tmp_git_repo / "README.md"
    readme.write_text("new content")
    stage_all(tmp_git_repo)

    sha = create_commit(
        tmp_git_repo,
        "Custom author commit",
        author_name="Custom User",
        author_email="custom@example.com",
    )

    repo = Repo(tmp_git_repo)
    commit = repo.commit(sha)
    assert commit.author.name == "Custom User"
    assert commit.author.email == "custom@example.com"


def test_get_head_sha(tmp_git_repo: Path) -> None:
    """Test getting HEAD SHA."""
    sha = get_head_sha(tmp_git_repo)
    assert len(sha) == FULL_SHA_LENGTH


def test_get_current_branch(tmp_git_repo: Path) -> None:
    """Test getting current branch name."""
    branch = get_current_branch(tmp_git_repo)
    assert branch in DEFAULT_BRANCHES


def test_append_to_gitignore(tmp_git_repo: Path) -> None:
    """Test appending patterns to .gitignore."""
    patterns = [".agent/", "*.log", "__pycache__/"]
    append_to_gitignore(tmp_git_repo, patterns)

    gitignore = tmp_git_repo / ".gitignore"
    content = gitignore.read_text()

    for pattern in patterns:
        assert pattern in content


def test_append_to_gitignore_existing(tmp_git_repo: Path) -> None:
    """Test appending to existing .gitignore without duplicates."""
    gitignore = tmp_git_repo / ".gitignore"
    gitignore.write_text(".existing\n")

    patterns = [".new/", ".existing"]
    append_to_gitignore(tmp_git_repo, patterns)

    content = gitignore.read_text()
    # .existing should not be duplicated
    assert content.count(".existing") == INITIAL_OCCURRENCE_COUNT
    assert ".new/" in content


def test_merge_base(tmp_git_repo: Path) -> None:
    """Test finding merge base between commits."""
    # Create two branches with commits
    # Create a new commit on main
    readme = tmp_git_repo / "README.md"
    readme.write_text("update 1")
    stage_all(tmp_git_repo)
    sha1 = create_commit(tmp_git_repo, "Commit 1")

    readme.write_text("update 2")
    stage_all(tmp_git_repo)
    sha2 = create_commit(tmp_git_repo, "Commit 2")

    # Merge base should be the initial commit (before both new commits)
    base = merge_base(tmp_git_repo, sha1, sha2)
    # Both shas should have the same merge base (the commit before them)
    base2 = merge_base(tmp_git_repo, sha2, sha1)
    assert base == base2


def test_push_without_remote(tmp_git_repo: Path) -> None:
    """Test that push fails gracefully without remote."""
    # Create a new branch
    repo = Repo(tmp_git_repo)
    repo.create_head("test-branch")

    with pytest.raises(GitOperationError):
        push(tmp_git_repo, remote="origin", branch="test-branch")
