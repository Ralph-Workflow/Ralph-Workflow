"""CLI option definitions for Ralph.

This module defines the option types and custom rich-click
components used throughout the CLI.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING

from rich.table import Table

from ralph.config.models import AgentConfig

if TYPE_CHECKING:
    from ralph.display.context import DisplayContext

AgentTable = Mapping[str, AgentConfig]


def _should_show_secondary(display_context: DisplayContext) -> bool:
    """Return False when in compact mode, else True."""
    return display_context.mode != "compact"


def display_agents_table(
    agents: AgentTable,
    display_context: DisplayContext,
) -> None:
    """Display a formatted table of agents.

    Args:
        agents: Dictionary of agent configurations.
        display_context: DisplayContext providing the console and mode for output.
    """
    c = display_context.console

    show_secondary = _should_show_secondary(display_context)
    table = Table(title="Configured Agents", show_header=True)
    table.add_column("Name", style="theme.cat.meta")
    table.add_column("Command")
    if show_secondary:
        table.add_column("Parser", style="theme.cat.cont")
        table.add_column("Can Commit", justify="center")

    for name, agent in agents.items():
        if show_secondary:
            can_commit_str = "yes" if agent.can_commit else "no"
            table.add_row(name, agent.cmd, str(agent.json_parser), can_commit_str)
        else:
            table.add_row(name, agent.cmd)

    c.print(table)


def display_providers_table(
    providers: list[str],
    display_context: DisplayContext,
) -> None:
    """Display a formatted table of providers.

    Args:
        providers: List of provider names.
        display_context: DisplayContext providing the console and mode for output.
    """
    c = display_context.console

    show_status = _should_show_secondary(display_context)
    table = Table(title="Available Providers", show_header=True)
    table.add_column("Provider", style="theme.cat.meta")
    if show_status:
        table.add_column("Status", justify="center")

    for provider in providers:
        if show_status:
            table.add_row(provider, "Available")
        else:
            table.add_row(provider)

    c.print(table)
