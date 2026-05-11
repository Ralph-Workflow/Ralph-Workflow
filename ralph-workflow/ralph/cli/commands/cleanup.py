"""Cleanup command — remove stale parallel worker namespaces after a hard-kill."""

from __future__ import annotations

import shutil
from typing import Annotated

import typer

from ralph.git.operations import find_repo_root


def cleanup(
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="List stale namespaces without removing them"),
    ] = False,
    force: Annotated[
        bool,
        typer.Option("--force", help="Remove without prompting for confirmation"),
    ] = False,
) -> None:
    """Remove stale per-worker namespaces under .agent/workers/ after a hard-kill.

    In same-workspace parallel mode, each worker writes to .agent/workers/<unit_id>/.
    These directories are normally cleaned up automatically, but a hard-kill may
    leave them behind.
    """
    try:
        repo_root = find_repo_root()
    except Exception as exc:
        typer.echo(f"Error: not in a git repository: {exc}", err=True)
        raise typer.Exit(1) from exc

    workers_dir = repo_root / ".agent" / "workers"
    if not workers_dir.exists():
        typer.echo("No stale worker namespaces found")
        raise typer.Exit(0)

    stale = sorted(d.name for d in workers_dir.iterdir() if d.is_dir())

    if not stale:
        typer.echo("No stale worker namespaces found")
        raise typer.Exit(0)

    if dry_run:
        typer.echo(f"Found {len(stale)} stale worker namespace(s) (dry-run, not removing):")
        for unit_id in stale:
            typer.echo(f"  .agent/workers/{unit_id}")
        raise typer.Exit(0)

    if not force:
        confirmed = typer.confirm(f"Remove {len(stale)} stale worker namespace(s)?")
        if not confirmed:
            typer.echo("Aborted")
            raise typer.Exit(0)

    removed = 0
    for unit_id in stale:
        target = workers_dir / unit_id
        shutil.rmtree(target, ignore_errors=True)
        removed += 1

    typer.echo(f"Removed {removed} stale worker namespace(s)")
