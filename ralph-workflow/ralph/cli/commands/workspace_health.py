"""``ralph workspace-health`` — operator-facing workspace health report (AC-11).

Prints one JSON object from
:func:`ralph.diagnostics.workspace_health.collect_workspace_health`
covering storage by category, freshness/readiness, coverage gaps,
active observation/refresh/cleanup/recovery work, watch-capacity
status, cleanup eligibility, and recreatability. Read-only: the
command never mutates the workspace.
"""

from __future__ import annotations

import json
from typing import Annotated

import typer

from ralph.diagnostics.workspace_health import collect_workspace_health


def workspace_health(
    workspace: Annotated[
        str,
        typer.Option("--workspace", help="Workspace root to inspect"),
    ] = ".",
) -> None:
    """Print a JSON workspace-health report for operators and agents (AC-11)."""
    from pathlib import Path

    payload = collect_workspace_health(Path(workspace))
    typer.echo(json.dumps(payload))


__all__ = ["workspace_health"]
