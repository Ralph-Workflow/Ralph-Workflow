"""CLI option definitions for Ralph.

This module defines the option types and custom rich-click
components used throughout the CLI.
"""

from __future__ import annotations

from collections.abc import Mapping

from rich.console import Console
from rich.table import Table

from ralph.config.models import AgentConfig

console = Console()


AgentTable = Mapping[str, AgentConfig]


def display_agents_table(agents: AgentTable, console: Console | None = None) -> None:
    """Display a formatted table of agents.

    Args:
        agents: Dictionary of agent configurations.
        console: Rich console for output.
    """
    c = console or Console()
    table = Table(title="Configured Agents", show_header=True)
    table.add_column("Name", style="cyan")
    table.add_column("Command")
    table.add_column("Parser", style="magenta")
    table.add_column("Can Commit", justify="center")

    for name, agent in agents.items():
        cmd = agent.cmd
        parser = str(agent.json_parser)
        can_commit = "yes" if agent.can_commit else "no"
        table.add_row(name, cmd, parser, can_commit)

    c.print(table)


def display_providers_table(providers: list[str], console: Console | None = None) -> None:
    """Display a formatted table of providers.

    Args:
        providers: List of provider names.
        console: Rich console for output.
    """
    c = console or Console()
    table = Table(title="Available Providers", show_header=True)
    table.add_column("Provider", style="cyan")

    for provider in providers:
        table.add_row(provider)

    c.print(table)
