"""Rich-based tabular display for ralph diagnostic commands.

This module provides formatted table output for various
list and display commands using the rich library.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from rich.panel import Panel
from rich.table import Table

from ralph.display.theme import make_console

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


def show_agents(config: UnifiedConfig, console: Console | None = None) -> None:
    """Render agent table for --list-agents.

    Args:
        config: Unified configuration containing agent definitions.
        console: Rich console for output.
    """
    c = console or make_console()
    table = Table(title="Configured Agents", show_header=True)
    table.add_column("Name", style="theme.cat.meta")
    table.add_column("Command")
    table.add_column("Parser", style="theme.cat.cont")
    table.add_column("Can Commit", justify="center")

    if not config.agents:
        table.add_row("[dim]No agents configured[/dim]", "", "", "")
    else:
        for name, agent in config.agents.items():
            table.add_row(
                name,
                agent.cmd,
                agent.json_parser.value,
                "yes" if agent.can_commit else "no",
            )

    c.print(table)


def show_providers(providers: list[str], console: Console | None = None) -> None:
    """Render providers table for --list-providers.

    Args:
        providers: List of provider names.
        console: Rich console for output.
    """
    c = console or make_console()
    table = Table(title="Available Providers", show_header=True)
    table.add_column("Provider", style="theme.cat.meta")
    table.add_column("Status", justify="center")

    if not providers:
        table.add_row("[dim]No providers available[/dim]", "")
    else:
        for provider in providers:
            table.add_row(provider, "[theme.status.success]Available[/theme.status.success]")

    c.print(table)


def show_config(config: UnifiedConfig, console: Console | None = None) -> None:
    """Render effective config for --check-config.

    Args:
        config: Unified configuration.
        console: Rich console for output.
    """
    c = console or make_console()
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
) -> None:
    """Render metrics table.

    Args:
        metrics: Dictionary of metric name to value.
        console: Rich console for output.
    """
    c = console or make_console()
    table = Table(title="Pipeline Metrics", show_header=True)
    table.add_column("Metric", style="theme.cat.meta")
    table.add_column("Value", justify="right", style="theme.status.success")

    for name, value in metrics.items():
        table.add_row(name, str(value))

    c.print(table)


def show_checkpoint_summary(
    options: CheckpointSummaryOptions,
    console: Console | None = None,
) -> None:
    """Render checkpoint summary.

    Args:
        options: Checkpoint summary options.
        console: Rich console for output.
    """
    c = console or make_console()
    table = Table(title="Checkpoint Summary", show_header=False)
    table.add_column("Property", style="theme.cat.meta")
    table.add_column("Value")

    table.add_row("Phase", options.phase)
    table.add_row("Iteration", f"{options.iteration}/{options.total_iterations}")
    table.add_row("Review Pass", f"{options.reviewer_pass}/{options.total_reviewer_passes}")

    c.print(table)
