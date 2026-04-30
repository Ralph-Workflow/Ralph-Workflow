"""Status display utilities for Ralph pipeline.

This module provides progress and status display using rich.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from rich.progress import BarColumn, Progress, SpinnerColumn, TaskProgressColumn, TextColumn
from rich.table import Table
from rich.text import Text

if TYPE_CHECKING:
    from ralph.display.context import DisplayContext


@dataclass(frozen=True)
class StatusSummary:
    """Summary data for status display."""

    phase: str
    iteration: int
    total_iterations: int
    reviewer_pass: int
    total_reviewer_passes: int
    metrics: dict[str, int]


def display_phase(
    phase: str,
    iteration: int,
    total: int,
    display_context: DisplayContext,
) -> None:
    """Display current phase.

    Args:
        phase: Current phase name.
        iteration: Current iteration number.
        total: Total iterations.
        display_context: DisplayContext providing the console for output.
    """
    c = display_context.console
    c.print(f"[theme.cat.meta]Phase:[/theme.cat.meta] {phase}")
    c.print(Text(f"Iteration {iteration} of {total}", style="theme.text.muted"))


def display_progress(
    current: int,
    total: int,
    phase: str,
    display_context: DisplayContext,
) -> Progress:
    """Create a progress bar for pipeline execution.

    Args:
        current: Current progress value.
        total: Total progress value.
        phase: Current phase name.
        display_context: DisplayContext providing the console for output.

    Returns:
        Progress bar instance.
    """
    c = display_context.console
    progress = Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        console=c,
    )
    progress.add_task(f"[theme.cat.meta]{phase}[/theme.cat.meta]", total=total, completed=current)
    return progress


def display_status_summary(
    summary: StatusSummary,
    display_context: DisplayContext,
) -> None:
    """Display a comprehensive status summary.

    Args:
        summary: Status summary data.
        display_context: DisplayContext providing the console for output.
    """
    c = display_context.console

    table = Table(
        title="Pipeline Status",
        show_header=False,
        title_style="theme.banner.title",
    )
    table.add_column("Property", style="theme.cat.meta")
    table.add_column("Value")

    table.add_row("Phase", summary.phase)
    table.add_row("Iteration", f"{summary.iteration}/{summary.total_iterations}")
    table.add_row("Review Pass", f"{summary.reviewer_pass}/{summary.total_reviewer_passes}")

    for key, value in summary.metrics.items():
        table.add_row(key, str(value))

    c.print(table)


def create_progress_bar(
    display_context: DisplayContext,
) -> Progress:
    """Create a configured progress bar.

    Args:
        display_context: DisplayContext providing the console for output.

    Returns:
        Configured Progress instance.
    """
    c = display_context.console
    return Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        console=c,
        expand=False,
    )
