"""Tests for git worktree availability preflight checks."""

import subprocess
from pathlib import Path

from ralph.git.worktree_preflight import check_worktree_supported


class FakeGitExecutor:
    def __init__(self, result: subprocess.CompletedProcess[str]) -> None:
        self.result = result

    def run(self, _action):
        return self.result


def test_check_worktree_supported_returns_true_for_normal_repo(tmp_path: Path) -> None:
    result = check_worktree_supported(
        repo_root=tmp_path,
        git=FakeGitExecutor(
            subprocess.CompletedProcess(
                args=["git", "worktree", "list", "--porcelain"],
                returncode=0,
                stdout=f"worktree {tmp_path}\n",
                stderr="",
            )
        ),
    )

    assert result.supported is True
    assert result.reason == ""


def test_check_worktree_supported_returns_actionable_message_for_shallow_repo(
    tmp_path: Path,
) -> None:
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    shallow_file = git_dir / "shallow"
    shallow_file.write_text("fake-shallow\n")

    result = check_worktree_supported(
        repo_root=tmp_path,
        git=FakeGitExecutor(
            subprocess.CompletedProcess(args=["git"], returncode=0, stdout="", stderr="")
        ),
    )

    assert result.supported is False
    assert "unshallow" in result.reason
