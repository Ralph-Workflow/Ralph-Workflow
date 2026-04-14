"""CLI option definitions for Ralph.

This module defines the option types and custom rich-click
components used throughout the CLI.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

import rich_click as click
from rich.console import Console
from rich.table import Table

from ralph.config.enums import (
    Verbosity,
)
from ralph.config.models import AgentConfig


class _AnyCallable(Protocol):
    """Protocol for any callable with any signature."""

    def __call__(self, *args: object, **kwargs: object) -> object: ...


console = Console()

# Mapping of numeric verbosity levels to enum values
_VERBOSITY_LEVELS = (
    Verbosity.QUIET,
    Verbosity.NORMAL,
    Verbosity.VERBOSE,
    Verbosity.FULL,
    Verbosity.DEBUG,
)


# Verbosity option with custom handling
class VerbosityOption(click.Option):
    """Custom option for verbosity that accepts both numeric and string values."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        """Initialize verbosity option."""
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]

    def process_value(self, ctx: click.Context, value: object) -> Verbosity:
        """Process the verbosity value.

        Args:
            ctx: Click context.
            value: Input value.

        Returns:
            Processed Verbosity enum value.
        """
        if value is None:
            return Verbosity.NORMAL

        if isinstance(value, Verbosity):
            return value

        if isinstance(value, str):
            verbosity = self._string_to_verbosity(value)
            if verbosity is not None:
                return verbosity

            verbosity = self._numeric_string_to_verbosity(value)
            if verbosity is not None:
                return verbosity

        return Verbosity.NORMAL

    def _string_to_verbosity(self, value: str) -> Verbosity | None:
        """Convert string value to Verbosity enum.

        Args:
            value: String value to convert.

        Returns:
            Verbosity enum value or None if not found.
        """
        value_lower = value.lower()
        for verbosity in Verbosity:
            if verbosity.value == value_lower:
                return verbosity
        return None

    def _numeric_string_to_verbosity(self, value: str) -> Verbosity | None:
        """Convert numeric string to Verbosity enum.

        Args:
            value: String value representing a number.

        Returns:
            Verbosity enum value or None if conversion fails.
        """
        try:
            level = int(value)
            if 0 <= level < len(_VERBOSITY_LEVELS):
                return _VERBOSITY_LEVELS[level]
            return Verbosity.DEBUG
        except ValueError:
            return None


# Custom option classes for better help display
def verbose_option(func: _AnyCallable) -> _AnyCallable:
    """Add verbose option decorator."""
    return click.option(
        "--verbose",
        "-v",
        count=True,
        help="Increase verbosity (-v, -vv, -vvv)",
    )(func)


def quiet_option(func: _AnyCallable) -> _AnyCallable:
    """Add quiet option decorator."""
    return click.option(
        "--quiet",
        "-q",
        is_flag=True,
        help="Suppress all output except errors",
    )(func)


def config_option(func: _AnyCallable) -> _AnyCallable:
    """Add config file option decorator."""
    return click.option(
        "--config",
        "-c",
        type=click.Path(exists=False, file_okay=True, dir_okay=False),
        help="Path to configuration file",
    )(func)


def developer_iters_option(func: _AnyCallable) -> _AnyCallable:
    """Add developer iterations option decorator."""
    return click.option(
        "--developer-iters",
        "-D",
        type=int,
        default=5,
        show_default=True,
        help="Number of developer agent iterations",
    )(func)


def reviewer_reviews_option(func: _AnyCallable) -> _AnyCallable:
    """Add reviewer reviews option decorator."""
    return click.option(
        "--reviewer-reviews",
        "-R",
        type=int,
        default=2,
        show_default=True,
        help="Number of review-fix cycles (0=skip review)",
    )(func)


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
