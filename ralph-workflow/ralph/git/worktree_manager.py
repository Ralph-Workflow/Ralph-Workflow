"""Manage per-work-unit git worktree lifecycle."""

import subprocess
from collections.abc import Sequence
from pathlib import Path

from ralph.git.subprocess_runner import run_git


class WorktreeExistsError(FileExistsError):
    """Raised when a requested worktree path already exists."""


class WorktreeManager:
    """Create, remove, and enumerate per-unit git worktrees."""

    def __init__(self, repo_root: Path) -> None:
        self.repo_root = repo_root
        self.worktrees_root = repo_root / ".worktrees"

    def create(self, unit_id: str, base_branch: str) -> Path:
        """Create a linked worktree for a unit from a base branch."""
        worktree_path = self.worktrees_root / unit_id
        if worktree_path.exists():
            raise WorktreeExistsError(
                f"Worktree already exists for unit '{unit_id}': {worktree_path}"
            )

        self.worktrees_root.mkdir(parents=True, exist_ok=True)
        branch_name = self._branch_name(unit_id)
        self._run_git(
            [
                "worktree",
                "add",
                "-b",
                branch_name,
                str(worktree_path.relative_to(self.repo_root)),
                base_branch,
            ]
        )
        return worktree_path

    def destroy(self, unit_id: str) -> None:
        """Remove a linked worktree for a unit if present."""
        worktree_path = self.worktrees_root / unit_id
        if not worktree_path.exists():
            return

        self._run_git(
            ["worktree", "remove", "--force", str(worktree_path.relative_to(self.repo_root))]
        )

    def list(self) -> list[str]:
        """Return live unit IDs for tracked Ralph worktrees."""
        result = self._run_git(["worktree", "list", "--porcelain"], capture_output=True)
        unit_ids: list[str] = []

        for line in result.stdout.splitlines():
            if not line.startswith("worktree "):
                continue
            worktree_path = Path(line.removeprefix("worktree ")).resolve()
            try:
                relative_path = worktree_path.relative_to(self.worktrees_root.resolve())
            except ValueError:
                continue
            if len(relative_path.parts) == 1:
                unit_ids.append(relative_path.parts[0])

        return sorted(unit_ids)

    def _run_git(
        self,
        args: Sequence[str],
        *,
        capture_output: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        operation = args[1] if len(args) > 1 else args[0] if args else "unknown"
        result = run_git(
            args,
            cwd=self.repo_root,
            label=f"git-worktree:{operation}",
            capture_output=capture_output,
            check=True,
        )
        return subprocess.CompletedProcess(
            args=list(result.args),
            returncode=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
        )

    @staticmethod
    def _branch_name(unit_id: str) -> str:
        return f"ralph/unit-{unit_id}"
