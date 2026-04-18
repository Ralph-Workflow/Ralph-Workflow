"""Status display utilities for Ralph pipeline.

This module provides progress and status display using rich.
"""

from __future__ import annotations

from dataclasses import dataclass

from rich.console import Console
from rich.progress import BarColumn, Progress, SpinnerColumn, TaskProgressColumn, TextColumn
from rich.table import Table


@dataclass(frozen=True)
class StatusSummary:
    """Summary data for status display."""

    phase: str
    iteration: int
    total_iterations: int
    reviewer_pass: int
    total_reviewer_passes: int
    metrics: dict[str, int]


def display_phase(phase: str, iteration: int, total: int, console: Console | None = None) -> None:
    """Display current phase.

    Args:
        phase: Current phase name.
        iteration: Current iteration number.
        total: Total iterations.
        console: Rich console for output.
    """
    c = console or Console()
    c.print(f"[cyan bold]Phase:[/cyan bold] {phase}")
    c.print(f"[dim]Iteration {iteration} of {total}[/dim]")


def display_progress(
    current: int,
    total: int,
    phase: str,
    console: Console | None = None,
) -> Progress:
    """Create a progress bar for pipeline execution.

    Args:
        current: Current progress value.
        total: Total progress value.
        phase: Current phase name.
        console: Rich console for output.

    Returns:
        Progress bar instance.
    """
    c = console or Console()
    progress = Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        console=c,
    )
    progress.add_task(f"[cyan]{phase}[/cyan]", total=total, completed=current)
    return progress


def display_status_summary(
    summary: StatusSummary,
    console: Console | None = None,
) -> None:
    """Display a comprehensive status summary.

    Args:
        summary: Status summary data.
        console: Rich console for output.
    """
    c = console or Console()

    table = Table(title="Pipeline Status", show_header=False)
    table.add_column("Property", style="cyan")
    table.add_column("Value")

    table.add_row("Phase", summary.phase)
    table.add_row("Iteration", f"{summary.iteration}/{summary.total_iterations}")
    table.add_row("Review Pass", f"{summary.reviewer_pass}/{summary.total_reviewer_passes}")

    for key, value in summary.metrics.items():
        table.add_row(key, str(value))

    c.print(table)


def create_progress_bar() -> Progress:
    """Create a configured progress bar.

    Returns:
        Configured Progress instance.
    """
    return Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        expand=False,
    )
