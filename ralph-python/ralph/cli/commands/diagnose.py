"""Diagnose command for Ralph CLI.

This module implements diagnostic commands to check the
environment and configuration.
"""

from __future__ import annotations

from pathlib import Path

from rich.console import Console
from rich.table import Table

from ralph.config.loader import load_config
from ralph.git.operations import find_repo_root, is_repo_clean

console = Console()


def diagnose_command(
    config_path: Path | None = None,
    cli_overrides: dict[str, object] | None = None,
) -> None:
    """Run diagnostics on the Ralph environment.

    Args:
        config_path: Optional path to config file.
        cli_overrides: CLI flag overrides.
    """
    console.print("\n[cyan bold]Ralph Diagnostics[/cyan bold]\n")

    # Check git repository
    _check_git_repo()

    # Check configuration
    _check_configuration(config_path, cli_overrides)

    # Check agent availability
    _check_agents(cli_overrides)

    # Check workspace files
    _check_workspace_files()

    console.print()


def _check_git_repo() -> None:
    """Check git repository status."""
    table = Table(title="Git Repository", show_header=False)
    table.add_column("Check", style="cyan")
    table.add_column("Status")

    try:
        repo_root = find_repo_root()
        table.add_row("Repository root", str(repo_root))
    except Exception as e:
        table.add_row("Repository", f"[red]Error: {e}[/red]")
        console.print(table)
        return

    try:
        clean = is_repo_clean(repo_root)
        if clean:
            table.add_row("Working tree", "[green]Clean[/green]")
        else:
            table.add_row("Working tree", "[yellow]Has uncommitted changes[/yellow]")
    except Exception as e:
        table.add_row("Working tree", f"[red]Error: {e}[/red]")

    console.print(table)


def _check_configuration(
    config_path: Path | None,
    cli_overrides: dict[str, object] | None,
) -> None:
    """Check configuration validity."""
    table = Table(title="Configuration", show_header=False)
    table.add_column("Check", style="cyan")
    table.add_column("Status")

    try:
        config = load_config(config_path, cli_overrides)
        table.add_row("Config loaded", "[green]Success[/green]")
        table.add_row("Developer iters", str(config.general.developer_iters))
        table.add_row("Reviewer reviews", str(config.general.reviewer_reviews))
        table.add_row("Review depth", config.general.review_depth.value)
        table.add_row("Checkpoint enabled", str(config.general.workflow.checkpoint_enabled))
    except Exception as e:
        table.add_row("Config loaded", f"[red]Error: {e}[/red]")

    console.print(table)


def _check_agents(cli_overrides: dict[str, object] | None) -> None:
    """Check agent availability."""
    table = Table(title="Agents", show_header=False)
    table.add_column("Agent", style="cyan")
    table.add_column("Status")

    try:
        config = load_config(None, cli_overrides)
        if not config.agents:
            table.add_row("No agents", "[yellow]No agents configured[/yellow]")
        else:
            for name, agent_config in config.agents.items():
                table.add_row(name, f"[green]Configured: {agent_config.cmd}[/green]")
    except Exception as e:
        table.add_row("Agents", f"[red]Error: {e}[/red]")

    console.print(table)


def _check_workspace_files() -> None:
    """Check workspace files."""
    table = Table(title="Workspace Files", show_header=False)
    table.add_column("File", style="cyan")
    table.add_column("Status")

    workspace_files = [
        ("PROMPT.md", "Implementation prompt"),
        (".agent/ralph-workflow.toml", "Local config"),
        (".agent/checkpoint.json", "Checkpoint"),
    ]

    for file_path, description in workspace_files:
        path = Path(file_path)
        if path.exists():
            size = path.stat().st_size
            table.add_row(f"{file_path} ({description})", f"[green]Exists ({size} bytes)[/green]")
        else:
            table.add_row(f"{file_path} ({description})", "[yellow]Not found[/yellow]")

    console.print(table)
