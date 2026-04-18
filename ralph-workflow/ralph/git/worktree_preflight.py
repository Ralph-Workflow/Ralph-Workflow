"""Pre-flight checks for git worktree availability."""

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import cast

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
        result = _run_git(repo_root, git, ["worktree", "list", "--porcelain"])
    except subprocess.CalledProcessError as error:
        if _git_stdout(repo_root, git, ["rev-parse", "--abbrev-ref", "HEAD"]) == "HEAD":
            return WorktreePreflightResult(
                supported=False,
                reason=(
                    "Git worktrees are unavailable from a detached HEAD. "
                    "Check out a branch and retry."
                ),
            )

        stderr_raw = cast("object", error.stderr)
        stdout_raw = cast("object", error.stdout)
        stderr_text = stderr_raw if isinstance(stderr_raw, str) else ""
        stdout_text = stdout_raw if isinstance(stdout_raw, str) else ""
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


def _run_git(
    repo_root: Path, git: GitExecutor, args: list[str]
) -> subprocess.CompletedProcess[str]:
    return git.run(
        lambda: subprocess.run(
            ["git", *args],
            cwd=repo_root,
            check=True,
            capture_output=True,
            text=True,
        )
    )


def _git_stdout(repo_root: Path, git: GitExecutor, args: list[str]) -> str:
    result = git.run(
        lambda: subprocess.run(
            ["git", *args],
            cwd=repo_root,
            check=False,
            capture_output=True,
            text=True,
        )
    )
    return result.stdout.strip()
