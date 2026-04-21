"""Init command for Ralph CLI.

This module implements the initialization command that sets up
Ralph in a repository.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from rich.console import Console
from rich.text import Text

import ralph.policy
from ralph.config.bootstrap import (
    ensure_global_config,
    ensure_global_mcp_config,
    ensure_local_configs,
)

console = Console()


def init_command(
    template: str | None = None,
    config_path: Path | None = None,
) -> None:
    """Initialize Ralph in the current working directory.

    Args:
        template: Optional template name (e.g. 'starter-template').
              Selects which PROMPT.md content to generate.
              Currently all templates use the same starter content.
        config_path: Optional path for config file.
    """
    target = Path.cwd()
    agent_dir = target / ".agent"
    agent_dir.mkdir(exist_ok=True)

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
        console.print(_status_text("Created", str(prompt_path), "green"))

    bundled_defaults = Path(ralph.policy.__file__).parent / "defaults"

    if config_path is not None and not config_path.exists():
        config_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(bundled_defaults / "ralph-workflow.toml"), str(config_path))
        console.print(_status_text("Created", str(config_path), "green"))
    elif config_path is None:
        for result in ensure_local_configs(agent_dir):
            if result.action == "created":
                console.print(_status_text("Created", str(result.path), "green"))

    for result in (ensure_global_config(), ensure_global_mcp_config()):
        if result.action == "created":
            console.print(_status_text("Created default config", str(result.path), "green"))

    template_label = template or "default"
    console.print(_status_text("Ralph initialized in", str(target), "cyan"))
    console.print(f"  [dim]Template:[/dim] {template_label}")
    console.print("\n[dim]Next steps:[/dim]")
    console.print("  1. Edit [cyan]PROMPT.md[/cyan] with your implementation task")
    console.print(
        "  2. (Optional) Override defaults in [cyan].agent/ralph-workflow.toml[/cyan]"
        " or [cyan]~/.config/ralph-workflow.toml[/cyan]"
    )
    console.print(
        "  3. (Optional) Configure MCP servers in [cyan].agent/mcp.toml[/cyan]"
        " or [cyan]~/.config/ralph-workflow-mcp.toml[/cyan]"
    )
    console.print("  4. Run [cyan]ralph[/cyan] to start the pipeline")
    console.print("\n[dim]To reset configs later: [cyan]ralph --regenerate-config[/cyan][/dim]")


def _status_text(label: str, detail: str, style: str) -> Text:
    text = Text()
    text.append(f"{label}:", style=style)
    text.append(" ")
    text.append(detail)
    return text
