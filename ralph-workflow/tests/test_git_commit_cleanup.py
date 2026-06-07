"""Unit tests for git commit cleanup operations."""

from __future__ import annotations

from typing import TYPE_CHECKING

from git import Repo

from ralph.git.commit_cleanup import (
    add_to_git_exclude,
    delete_file_from_repo,
    ensure_git_initialized,
)

if TYPE_CHECKING:
    from pathlib import Path


def test_delete_file_from_repo_removes_untracked_file(tmp_git_repo: Path) -> None:
    """Test that delete_file_from_repo removes an untracked file."""
    binary = tmp_git_repo / "binary.exe"
    binary.write_text("binary content")

    assert binary.exists()

    delete_file_from_repo(tmp_git_repo, "binary.exe")

    assert not binary.exists()


def test_delete_file_from_repo_unstages_staged_file(tmp_git_repo: Path) -> None:
    """Test that delete_file_from_repo unstages and removes a staged file."""
    binary = tmp_git_repo / "staged.bin"
    binary.write_text("staged content")

    repo = Repo(tmp_git_repo)
    try:
        repo.index.add(["staged.bin"])
        repo.index.commit("add staged file")

        # Modify after commit
        binary.write_text("modified content")

        delete_file_from_repo(tmp_git_repo, "staged.bin")

        assert not binary.exists()
        # Verify file is no longer tracked in the index
        ls_files = repo.git.ls_files("--stage", "staged.bin")
        assert ls_files.strip() == ""
    finally:
        repo.close()


def test_delete_file_from_repo_ignores_nonexistent_path(tmp_git_repo: Path) -> None:
    """Test that delete_file_from_repo does not raise for nonexistent files."""
    # Should not raise
    delete_file_from_repo(tmp_git_repo, "nonexistent.file")
    delete_file_from_repo(tmp_git_repo, "path/does/not/exist")


def test_add_to_git_exclude_creates_file_if_missing(tmp_git_repo: Path) -> None:
    """Test that add_to_git_exclude creates .git/info/exclude if missing."""
    exclude_path = tmp_git_repo / ".git" / "info" / "exclude"

    add_to_git_exclude(tmp_git_repo, ["*.pyc"])

    assert exclude_path.exists()
    content = exclude_path.read_text()
    assert "*.pyc" in content


def test_add_to_git_exclude_appends_new_patterns(tmp_git_repo: Path) -> None:
    """Test that add_to_git_exclude appends patterns without removing existing ones."""
    exclude_path = tmp_git_repo / ".git" / "info" / "exclude"
    exclude_path.parent.mkdir(parents=True, exist_ok=True)
    exclude_path.write_text("existing pattern\n")

    add_to_git_exclude(tmp_git_repo, ["*.pyc", "*.log"])

    content = exclude_path.read_text()
    assert "existing pattern" in content
    assert "*.pyc" in content
    assert "*.log" in content


def test_add_to_git_exclude_deduplicates_patterns(tmp_git_repo: Path) -> None:
    """Test that add_to_git_exclude does not add duplicate patterns."""
    exclude_path = tmp_git_repo / ".git" / "info" / "exclude"
    exclude_path.parent.mkdir(parents=True, exist_ok=True)
    exclude_path.write_text("*.pyc\n")

    add_to_git_exclude(tmp_git_repo, ["*.pyc"])

    lines = [line for line in exclude_path.read_text().splitlines() if line]
    assert lines.count("*.pyc") == 1


def test_ensure_git_initialized_inits_non_repo_directory(tmp_path: Path) -> None:
    """Test that ensure_git_initialized initializes a non-git directory."""
    non_repo = tmp_path / "non_repo"
    non_repo.mkdir()

    assert not (non_repo / ".git").exists()

    ensure_git_initialized(non_repo)

    assert (non_repo / ".git").exists()
    # Verify it's a valid git repo
    with Repo(non_repo) as repo:
        assert repo.active_branch.name in ("main", "master", "HEAD")


def test_ensure_git_initialized_is_noop_for_existing_repo(tmp_git_repo: Path) -> None:
    """Test that ensure_git_initialized does nothing for existing repos."""
    # Should not raise
    ensure_git_initialized(tmp_git_repo)

    # Repo should still be valid
    with Repo(tmp_git_repo) as repo:
        assert repo.active_branch.name in ("main", "master", "HEAD")
        # Commit should still exist
        assert repo.head.commit.hexsha
