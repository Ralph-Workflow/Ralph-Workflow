"""Pre-flight checks for git worktree availability."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ralph.git.subprocess_runner import run_git

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.git.executor import GitExecutor


@dataclass(frozen=True)
class WorktreePreflightResult:
    supported: bool
    reason: str


def _is_shallow(repo_root: Path) -> bool:
    """Return True if the repository has a non-empty .git/shallow file."""
    shallow = repo_root / ".git" / "shallow"
    return shallow.exists() and shallow.stat().st_size > 0


def check_worktree_supported(repo_root: Path, git: GitExecutor) -> WorktreePreflightResult:
    """Return whether the repository can safely use git worktrees."""
    if _is_shallow(repo_root):
        return WorktreePreflightResult(
            supported=False,
            reason=(
                "Git worktrees are unavailable in shallow repositories. "
                "Run `git fetch --unshallow` and retry."
            ),
        )

    try:
        result = _run_git(repo_root, ["worktree", "list", "--porcelain"])
    except subprocess.CalledProcessError as error:
        if _git_stdout(repo_root, ["rev-parse", "--abbrev-ref", "HEAD"]) == "HEAD":
            return WorktreePreflightResult(
                supported=False,
                reason=(
                    "Git worktrees are unavailable from a detached HEAD. "
                    "Check out a branch and retry."
                ),
            )

        raw_stderr: object = error.stderr
        raw_stdout: object = error.stdout
        stderr_text = raw_stderr if isinstance(raw_stderr, str) else ""
        stdout_text = raw_stdout if isinstance(raw_stdout, str) else ""
        stderr = stderr_text.strip() or stdout_text.strip() or str(error)
        return WorktreePreflightResult(
            supported=False,
            reason=f"Git worktree preflight failed: {stderr}",
        )

    if any(line.startswith("worktree ") for line in result.stdout.splitlines()):
        return WorktreePreflightResult(supported=True, reason="")

    return WorktreePreflightResult(
        supported=False,
        reason="Git worktree preflight failed: `git worktree list` returned invalid output.",
    )


def _run_git(repo_root: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    result = run_git(
        args,
        cwd=repo_root,
        label="git-worktree-preflight",
        check=True,
        capture_output=True,
        text=True,
    )
    return subprocess.CompletedProcess(
        args=list(result.args),
        returncode=result.returncode,
        stdout=result.stdout,
        stderr=result.stderr,
    )


def _git_stdout(repo_root: Path, args: list[str]) -> str:
    result = run_git(
        args,
        cwd=repo_root,
        label="git-worktree-preflight",
        check=False,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()
