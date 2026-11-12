from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from ralph.diagnostics.workspace_health import collect_workspace_health
from ralph.display.context import DisplayContext, make_display_context
from ralph.display.parallel_display import resolve_active_display


def workspace_health(
    workspace: Annotated[
        str,
        typer.Option("--workspace", help="Workspace root to inspect"),
    ] = ".",
) -> None:
    _emit_workspace_health(workspace)


def _emit_workspace_health(
    workspace: str, *, display_context: DisplayContext | None = None
) -> None:
    ctx = display_context if display_context is not None else make_display_context()
    display = resolve_active_display(None, ctx)
    payload = collect_workspace_health(Path(workspace))
    display.emit_json_payload(payload)


__all__ = ["workspace_health"]
