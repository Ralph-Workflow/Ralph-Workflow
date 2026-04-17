"""Tests for git worktree availability preflight checks."""

import subprocess
from pathlib import Path

import pytest

from ralph.git.executor import GitExecutor
from ralph.git.worktree_preflight import check_worktree_supported


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True)
    subprocess.run(["git", "branch", "-m", "main"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "--allow-empty", "-m", "init"], cwd=tmp_path, check=True)
    return tmp_path


def test_check_worktree_supported_returns_true_for_normal_repo(git_repo: Path) -> None:
    result = check_worktree_supported(repo_root=git_repo, git=GitExecutor())

    assert result.supported is True
    assert result.reason == ""


def test_check_worktree_supported_returns_actionable_message_for_shallow_repo(
    git_repo: Path,
) -> None:
    shallow_file = git_repo / ".git" / "shallow"
    shallow_file.write_text("fake-shallow\n")

    result = check_worktree_supported(repo_root=git_repo, git=GitExecutor())

    assert result.supported is False
    assert "unshallow" in result.reason
