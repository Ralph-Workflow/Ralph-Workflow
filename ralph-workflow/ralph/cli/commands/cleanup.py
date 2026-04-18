"""Cleanup command — remove orphaned git worktrees after a hard-kill."""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING, Annotated

import typer

if TYPE_CHECKING:
    from pathlib import Path

from ralph.git.executor import GitExecutor
from ralph.git.operations import find_repo_root
from ralph.git.worktree_manager import WorktreeManager


def cleanup(
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="List orphaned worktrees without removing them"),
    ] = False,
    force: Annotated[
        bool,
        typer.Option("--force", help="Remove without prompting for confirmation"),
    ] = False,
) -> None:
    """Remove orphaned git worktrees and their tracking branches."""
    try:
        repo_root = find_repo_root()
    except Exception as exc:
        typer.echo(f"Error: not in a git repository: {exc}", err=True)
        raise typer.Exit(1) from exc

    worktrees_dir = repo_root / ".worktrees"
    if not worktrees_dir.exists():
        typer.echo("No orphaned worktrees found")
        raise typer.Exit(0)

    orphaned = sorted(
        d.name for d in worktrees_dir.iterdir() if d.is_dir() and d.name.startswith("unit-")
    )

    if not orphaned:
        typer.echo("No orphaned worktrees found")
        raise typer.Exit(0)

    if dry_run:
        typer.echo(f"Found {len(orphaned)} orphaned worktree(s) (dry-run, not removing):")
        for unit_id in orphaned:
            typer.echo(f"  .worktrees/{unit_id}")
        raise typer.Exit(0)

    if not force:
        confirmed = typer.confirm(f"Remove {len(orphaned)} orphaned worktree(s)?")
        if not confirmed:
            typer.echo("Aborted")
            raise typer.Exit(0)

    git = GitExecutor()
    manager = WorktreeManager(repo_root, git)
    removed = 0
    for unit_id in orphaned:
        branch = f"ralph/{unit_id}"
        manager.destroy(unit_id)
        _delete_branch(git, repo_root, branch)
        removed += 1

    typer.echo(f"Removed {removed} worktree(s)")


def _delete_branch(git: GitExecutor, repo_root: Path, branch: str) -> None:
    result = git.run(
        lambda: subprocess.run(
            ["git", "branch", "-D", branch],
            cwd=repo_root,
            check=False,
            capture_output=True,
            text=True,
        )
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        typer.echo(f"Warning: failed to delete branch {branch}: {detail}", err=True)
