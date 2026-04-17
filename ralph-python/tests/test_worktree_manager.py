"""Tests for per-unit git worktree lifecycle management."""

import subprocess
from pathlib import Path

import pytest

from ralph.git.executor import GitExecutor
from ralph.git.worktree_manager import WorktreeExistsError, WorktreeManager


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True)
    subprocess.run(["git", "branch", "-m", "main"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "--allow-empty", "-m", "init"], cwd=tmp_path, check=True)
    return tmp_path


def test_create_and_list_worktree(git_repo: Path) -> None:
    manager = WorktreeManager(repo_root=git_repo, git=GitExecutor())

    worktree_path = manager.create(unit_id="alpha", base_branch="main")

    assert worktree_path == git_repo / ".worktrees" / "alpha"
    assert worktree_path.is_dir()
    assert manager.list() == ["alpha"]

    branches = subprocess.run(
        ["git", "branch", "--list", "ralph/unit-alpha"],
        cwd=git_repo,
        check=True,
        capture_output=True,
        text=True,
    )
    assert branches.stdout.strip().endswith("ralph/unit-alpha")


def test_create_raises_when_worktree_path_exists(git_repo: Path) -> None:
    manager = WorktreeManager(repo_root=git_repo, git=GitExecutor())
    existing_path = git_repo / ".worktrees" / "alpha"
    existing_path.mkdir(parents=True)

    with pytest.raises(WorktreeExistsError, match="alpha"):
        manager.create(unit_id="alpha", base_branch="main")


def test_destroy_removes_worktree_and_list_entry(git_repo: Path) -> None:
    manager = WorktreeManager(repo_root=git_repo, git=GitExecutor())
    manager.create(unit_id="alpha", base_branch="main")

    manager.destroy(unit_id="alpha")

    assert not (git_repo / ".worktrees" / "alpha").exists()
    assert manager.list() == []
