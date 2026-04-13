"""Init command for Ralph CLI.

This module implements the initialization command that sets up
Ralph in a repository.
"""

from __future__ import annotations

from pathlib import Path

from rich.console import Console

console = Console()

INIT_TEMPLATE = '''# Ralph Configuration

[general]
# Developer iterations (default: 5)
developer_iters = 5

# Reviewer reviews (default: 2)
reviewer_reviews = 2

# Review depth: standard, comprehensive, security, incremental
review_depth = "standard"

# Enable checkpoint/resume
checkpoint_enabled = true

# Isolation mode (prevent context contamination)
isolation_mode = true
'''


def init_command(
    path: str | None = None,
    config_path: Path | None = None,
) -> None:
    """Initialize Ralph in a directory.

    Args:
        path: Optional path to initialize (defaults to current directory).
        config_path: Optional path for config file.
    """
    target = Path(path) if path else Path.cwd()

    if not target.exists():
        target.mkdir(parents=True, exist_ok=True)

    # Create .agent directory
    agent_dir = target / ".agent"
    agent_dir.mkdir(exist_ok=True)

    # Create PROMPT.md if it doesn't exist
    prompt_path = target / "PROMPT.md"
    if not prompt_path.exists():
        prompt_path.write_text(
            "# Implementation Prompt\n\n"
            "Describe what you want to implement here.\n\n"
            "## Requirements\n\n"
            "- Requirement 1\n"
            "- Requirement 2\n\n"
            "## Acceptance Criteria\n\n"
            "- Criterion 1\n"
            "- Criterion 2\n",
            encoding="utf-8",
        )
        console.print(f"[green]Created[/green] {prompt_path}")

    # Create local config if requested or if no global config exists
    config_file = config_path or (agent_dir / "ralph-workflow.toml")
    if not config_file.exists():
        config_file.write_text(INIT_TEMPLATE, encoding="utf-8")
        console.print(f"[green]Created[/green] {config_file}")

    console.print(f"[cyan]Ralph[/cyan] initialized in {target}")
    console.print("\n[dim]Next steps:[/dim]")
    console.print("  1. Edit [cyan]PROMPT.md[/cyan] with your implementation task")
    console.print("  2. Configure agents in [cyan].agent/ralph-workflow.toml[/cyan]")
    console.print("  3. Run [cyan]ralph[/cyan] to start the pipeline")
