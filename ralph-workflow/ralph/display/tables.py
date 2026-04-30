"""Rich-based tabular display for ralph diagnostic commands.

This module provides formatted table output for various
list and display commands using the rich library.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from ralph.display.context import DisplayContext, make_display_context

if TYPE_CHECKING:
    from rich.console import Console

    from ralph.config.models import UnifiedConfig


@dataclass(frozen=True)
class CheckpointSummaryOptions:
    """Options for checkpoint summary display.

    Attributes:
        phase: Current pipeline phase.
        iteration: Current iteration.
        total_iterations: Total iterations.
        reviewer_pass: Current reviewer pass.
        total_reviewer_passes: Total reviewer passes.
    """

    phase: str
    iteration: int
    total_iterations: int
    reviewer_pass: int
    total_reviewer_passes: int


def _resolve_console(
    console: Console | None,
    display_context: DisplayContext | None,
) -> Console:
    if console is not None:
        return console
    if display_context is not None:
        return display_context.console
    return make_display_context().console


def _table_expand(display_context: DisplayContext | None) -> bool:
    if display_context is None:
        return False
    return display_context.mode == "wide"


def show_agents(
    config: UnifiedConfig,
    console: Console | None = None,
    display_context: DisplayContext | None = None,
) -> None:
    """Render agent table for --list-agents.

    Args:
        config: Unified configuration containing agent definitions.
        console: Rich console for output.
        display_context: Optional display context for adaptive layout.
    """
    c = _resolve_console(console, display_context)
    expand = _table_expand(display_context)
    table = Table(
        title="Configured Agents",
        show_header=True,
        expand=expand,
        show_lines=False,
        title_style="theme.banner.title",
        header_style="theme.text.emphasis",
    )
    table.add_column("Name", style="theme.cat.meta")
    table.add_column("Command")
    table.add_column("Parser", style="theme.cat.cont")
    table.add_column("Can Commit", justify="center")

    if not config.agents:
        table.add_row(Text("No agents configured", style="theme.text.muted"), "", "", "")
    else:
        for name, agent in config.agents.items():
            table.add_row(
                name,
                agent.cmd,
                agent.json_parser.value,
                "yes" if agent.can_commit else "no",
            )

    c.print(table)


def show_providers(
    providers: list[str],
    console: Console | None = None,
    display_context: DisplayContext | None = None,
) -> None:
    """Render providers table for --list-providers.

    Args:
        providers: List of provider names.
        console: Rich console for output.
        display_context: Optional display context for adaptive layout.
    """
    c = _resolve_console(console, display_context)
    expand = _table_expand(display_context)
    table = Table(
        title="Available Providers",
        show_header=True,
        expand=expand,
        show_lines=False,
        title_style="theme.banner.title",
        header_style="theme.text.emphasis",
    )
    table.add_column("Provider", style="theme.cat.meta")
    table.add_column("Status", justify="center")

    if not providers:
        table.add_row(Text("No providers available", style="theme.text.muted"), "")
    else:
        for provider in providers:
            t = Text("Available", style="theme.status.success")
            table.add_row(provider, t)

    c.print(table)


def show_config(
    config: UnifiedConfig,
    console: Console | None = None,
    display_context: DisplayContext | None = None,
) -> None:
    """Render effective config for --check-config.

    Args:
        config: Unified configuration.
        console: Rich console for output.
        display_context: Optional display context for adaptive layout.
    """
    c = _resolve_console(console, display_context)
    config_json = config.model_dump_json(indent=2)
    c.print(
        Panel(
            config_json,
            title="Effective Configuration",
            border_style="theme.phase.planning",
        )
    )


def show_metrics(
    metrics: dict[str, int],
    console: Console | None = None,
    display_context: DisplayContext | None = None,
) -> None:
    """Render metrics table.

    Args:
        metrics: Dictionary of metric name to value.
        console: Rich console for output.
        display_context: Optional display context for adaptive layout.
    """
    c = _resolve_console(console, display_context)
    expand = _table_expand(display_context)
    table = Table(
        title="Pipeline Metrics",
        show_header=True,
        expand=expand,
        show_lines=False,
        title_style="theme.banner.title",
        header_style="theme.text.emphasis",
    )
    table.add_column("Metric", style="theme.cat.meta")
    table.add_column("Value", justify="right", style="theme.status.success")

    for name, value in metrics.items():
        table.add_row(name, str(value))

    c.print(table)


def show_checkpoint_summary(
    options: CheckpointSummaryOptions,
    console: Console | None = None,
    display_context: DisplayContext | None = None,
) -> None:
    """Render checkpoint summary.

    Args:
        options: Checkpoint summary options.
        console: Rich console for output.
        display_context: Optional display context for adaptive layout.
    """
    c = _resolve_console(console, display_context)
    expand = _table_expand(display_context)
    table = Table(
        title="Checkpoint Summary",
        show_header=False,
        expand=expand,
        show_lines=False,
        title_style="theme.banner.title",
    )
    table.add_column("Property", style="theme.cat.meta")
    table.add_column("Value")

    table.add_row("Phase", options.phase)
    table.add_row("Iteration", f"{options.iteration}/{options.total_iterations}")
    table.add_row("Review Pass", f"{options.reviewer_pass}/{options.total_reviewer_passes}")

    c.print(table)
