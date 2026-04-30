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

if TYPE_CHECKING:
    from ralph.config.models import UnifiedConfig
    from ralph.display.context import DisplayContext


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


def _should_show_secondary(display_context: DisplayContext) -> bool:
    """Return False when in compact mode, else True."""
    return display_context.mode != "compact"


def _table_expand(display_context: DisplayContext) -> bool:
    """Return True when in wide mode (tables should expand)."""
    return display_context.mode == "wide"


def show_agents(
    config: UnifiedConfig,
    display_context: DisplayContext,
) -> None:
    """Render agent table for --list-agents.

    Args:
        config: Unified configuration containing agent definitions.
        display_context: DisplayContext providing the console and mode for output.
    """
    c = display_context.console
    expand = _table_expand(display_context)
    show_secondary = _should_show_secondary(display_context)
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
    if show_secondary:
        table.add_column("Parser", style="theme.cat.cont")
        table.add_column("Can Commit", justify="center")

    if not config.agents:
        if show_secondary:
            table.add_row(Text("No agents configured", style="theme.text.muted"), "", "", "")
        else:
            table.add_row(Text("No agents configured", style="theme.text.muted"), "")
    else:
        for name, agent in config.agents.items():
            if show_secondary:
                table.add_row(
                    name,
                    agent.cmd,
                    agent.json_parser.value,
                    "yes" if agent.can_commit else "no",
                )
            else:
                table.add_row(name, agent.cmd)

    c.print(table)


def show_providers(
    providers: list[str],
    display_context: DisplayContext,
) -> None:
    """Render providers table for --list-providers.

    Args:
        providers: List of provider names.
        display_context: DisplayContext providing the console and mode for output.
    """
    c = display_context.console
    expand = _table_expand(display_context)
    show_status = _should_show_secondary(display_context)
    table = Table(
        title="Available Providers",
        show_header=True,
        expand=expand,
        show_lines=False,
        title_style="theme.banner.title",
        header_style="theme.text.emphasis",
    )
    table.add_column("Provider", style="theme.cat.meta")
    if show_status:
        table.add_column("Status", justify="center")

    if not providers:
        if show_status:
            table.add_row(Text("No providers available", style="theme.text.muted"), "")
        else:
            table.add_row(Text("No providers available", style="theme.text.muted"))
    else:
        for provider in providers:
            if show_status:
                t = Text("Available", style="theme.status.success")
                table.add_row(provider, t)
            else:
                table.add_row(provider)

    c.print(table)


def show_config(
    config: UnifiedConfig,
    display_context: DisplayContext,
) -> None:
    """Render effective config for --check-config.

    Args:
        config: Unified configuration.
        display_context: DisplayContext providing the console and mode for output.
    """
    c = display_context.console
    config_json = config.model_dump_json(indent=2)
    if display_context.mode == "compact":
        c.print(config_json)
    else:
        c.print(
            Panel(
                config_json,
                title="Effective Configuration",
                border_style="theme.phase.planning",
            )
        )


def show_metrics(
    metrics: dict[str, int],
    display_context: DisplayContext,
) -> None:
    """Render metrics table.

    Args:
        metrics: Dictionary of metric name to value.
        display_context: DisplayContext providing the console and mode for output.
    """
    c = display_context.console
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
    display_context: DisplayContext,
) -> None:
    """Render checkpoint summary.

    Args:
        options: Checkpoint summary options.
        display_context: DisplayContext providing the console and mode for output.
    """
    c = display_context.console
    expand = _table_expand(display_context)
    show_secondary = _should_show_secondary(display_context)
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
    if show_secondary:
        table.add_row("Review Pass", f"{options.reviewer_pass}/{options.total_reviewer_passes}")

    c.print(table)
