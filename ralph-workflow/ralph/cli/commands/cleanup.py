"""Cleanup command — remove stale parallel worker namespaces after a hard-kill."""

from __future__ import annotations

import shutil
from typing import Annotated

import typer

from ralph.git.operations import find_repo_root
from ralph.mcp.tools.exec_overlay import _get_private_exec_base
from ralph.mcp.tools.exec_sandbox import ExecSandboxManager


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
    stale = (
        sorted(d.name for d in workers_dir.iterdir() if d.is_dir())
        if workers_dir.exists()
        else []
    )
    exec_cache_base = _get_private_exec_base()
    has_exec_cache_entries = exec_cache_base.exists() and any(exec_cache_base.iterdir())

    if not stale and not has_exec_cache_entries:
        typer.echo("No stale worker namespaces found")
        raise typer.Exit(0)

    if dry_run:
        if stale:
            typer.echo(
                f"Found {len(stale)} stale worker namespace(s) (dry-run, not removing):"
            )
            for unit_id in stale:
                typer.echo(f"  .agent/workers/{unit_id}")
        if has_exec_cache_entries:
            typer.echo(
                "Detected global exec cache entries under "
                f"{exec_cache_base} (dry-run, not removing)"
            )
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

    exec_summary = ExecSandboxManager(base_dir=exec_cache_base).cleanup_base()

    if removed > 0:
        typer.echo(f"Removed {removed} stale worker namespace(s)")
    if exec_summary.removed_paths > 0:
        typer.echo(
            "Pruned stale exec cache entries: "
            f"{exec_summary.removed_paths} path(s), {exec_summary.removed_bytes} byte(s) removed"
        )
