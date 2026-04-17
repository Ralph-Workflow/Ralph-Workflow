from __future__ import annotations

from typing import TYPE_CHECKING, TypedDict

from rich.table import Table

from ralph.pipeline.worker_state import WorkerStatus

if TYPE_CHECKING:
    from rich.console import RenderableType


class DashboardState(TypedDict):
    unit_id: str
    status: WorkerStatus
    elapsed_s: float
    last_output: str
    dropped: int


_STATUS_LABELS = {
    WorkerStatus.PENDING: "WAIT",
    WorkerStatus.RUNNING: "RUN",
    WorkerStatus.SUCCEEDED: "DONE",
    WorkerStatus.FAILED: "FAIL",
    WorkerStatus.CANCELLED: "WAIT",
}

_STATUS_COLORS = {
    WorkerStatus.PENDING: "dim",
    WorkerStatus.RUNNING: "cyan",
    WorkerStatus.SUCCEEDED: "green",
    WorkerStatus.FAILED: "red bold",
    WorkerStatus.CANCELLED: "dim",
}

_MAX_LAST_OUTPUT = 80


def render_dashboard(state: dict[str, DashboardState]) -> RenderableType:
    table = Table(show_header=True, title="Pipeline Dashboard")
    table.add_column("Unit ID", style="cyan")
    table.add_column("Status")
    table.add_column("Elapsed", justify="right")
    table.add_column("Last Output")

    for unit_state in state.values():
        status = unit_state["status"]
        label = _STATUS_LABELS[status]
        color = _STATUS_COLORS[status]
        elapsed = f"{unit_state['elapsed_s']:.1f}"
        last_output = unit_state["last_output"]
        if len(last_output) > _MAX_LAST_OUTPUT:
            last_output = last_output[:_MAX_LAST_OUTPUT] + "…"

        table.add_row(
            unit_state["unit_id"],
            f"[{color}]{label}[/{color}]",
            elapsed,
            last_output,
        )

    total_dropped = sum(u["dropped"] for u in state.values())
    if total_dropped > 0:
        table.add_row("", "", "", f"[dim]dropped: {total_dropped} lines[/dim]")

    return table
