"""Tests for git worktree availability preflight checks."""

import subprocess
from pathlib import Path
from unittest.mock import patch

from ralph.git.worktree_preflight import check_worktree_supported


def _make_completed(stdout: str = "", returncode: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=["git", "worktree", "list", "--porcelain"],
        returncode=returncode,
        stdout=stdout,
        stderr="",
    )


def test_check_worktree_supported_returns_true_for_normal_repo(tmp_path: Path) -> None:
    with patch(
        "ralph.git.worktree_preflight._run_git",
        return_value=_make_completed(stdout=f"worktree {tmp_path}\n"),
    ):
        result = check_worktree_supported(repo_root=tmp_path, git=None)  # type: ignore[arg-type]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library

    assert result.supported is True
    assert result.reason == ""


def test_check_worktree_supported_returns_actionable_message_for_shallow_repo(
    tmp_path: Path,
) -> None:
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    shallow_file = git_dir / "shallow"
    shallow_file.write_text("fake-shallow\n")

    result = check_worktree_supported(repo_root=tmp_path, git=None)  # type: ignore[arg-type]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library

    assert result.supported is False
    assert "unshallow" in result.reason
