"""Cleanup command — remove stale parallel worker namespaces after a hard-kill.

P2 (wt-028-display S-14): all visible output routes through the shared
``ParallelDisplay`` surface (``emit_status`` / ``emit_warning`` /
``emit_blank_line``) so the drift-prevention suite can verify no
command reaches the terminal through its own path. The interactive
confirmation prompt (``typer.confirm``) is the only side-channel that
remains — the prompt is operator input, not display output.
"""

from __future__ import annotations

import shutil
from typing import Annotated

import typer

from ralph.display.context import make_display_context
from ralph.display.parallel_display import resolve_active_display
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
    ctx = make_display_context()
    display = resolve_active_display(None, ctx)
    try:
        repo_root = find_repo_root()
    except Exception as exc:
        display.emit_warning(f"Error: not in a git repository: {exc}")
        raise typer.Exit(1) from exc

    workers_dir = repo_root / ".agent" / "workers"
    stale = (
        sorted(d.name for d in workers_dir.iterdir() if d.is_dir()) if workers_dir.exists() else []
    )

    if not stale:
        display.emit_status("No stale worker namespaces found")
        raise typer.Exit(0)

    if dry_run:
        display.emit_status(
            f"Found {len(stale)} stale worker namespace(s) (dry-run, not removing):"
        )
        for unit_id in stale:
            display.emit_status(f"  .agent/workers/{unit_id}")
        raise typer.Exit(0)

    if not force:
        # ``typer.confirm`` is operator input (interactive prompt), not
        # display output. Per S-14 the only allowed side-channel is the
        # interactive confirmation, so we keep this verbatim.
        confirmed = typer.confirm(f"Remove {len(stale)} stale worker namespace(s)?")
        if not confirmed:
            display.emit_status("Aborted")
            raise typer.Exit(0)

    removed = 0
    for unit_id in stale:
        target = workers_dir / unit_id
        # filesystem-write-ok: explicit CLI removal of user-selected Ralph Workflow run data
        shutil.rmtree(target, ignore_errors=True)
        removed += 1

    if removed > 0:
        display.emit_status(f"Removed {removed} stale worker namespace(s)")
