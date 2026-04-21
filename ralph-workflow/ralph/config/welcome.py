"""First-run welcome banner and agent availability helper."""

from __future__ import annotations

import shutil
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from rich.console import Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

if TYPE_CHECKING:
    from ralph.config.bootstrap import BootstrapResult
    from ralph.config.models import AgentConfig


@runtime_checkable
class _HasListAgents(Protocol):
    """Protocol for objects with a list_agents method."""

    def list_agents(self) -> list[AgentConfig]:
        ...


def _check_agent_availability(registry: _HasListAgents) -> list[tuple[str, bool]]:
    """Check which agents are available on PATH.

    Args:
        registry: Object with list_agents() method returning iterable of
            AgentConfig-like objects with a .cmd attribute.

    Returns:
        List of (agent_name, is_available) tuples.
    """
    results: list[tuple[str, bool]] = []
    agents = registry.list_agents()
    for agent in agents:
        cmd = agent.cmd
        if not cmd:
            results.append((agent.display_name or agent.cmd or "unknown", False))
            continue
        first_word = cmd.split(maxsplit=1)[0]
        results.append((agent.display_name or first_word, shutil.which(first_word) is not None))
    return results


def _build_agent_availability_content(
    agent_registry: _HasListAgents | None,
) -> list[object]:
    """Build agent availability content or generic PATH message."""
    content: list[object] = []
    if agent_registry is not None:
        try:
            availability = _check_agent_availability(agent_registry)
            avail_table = Table(show_header=True, header_style="bold cyan", box=None)
            avail_table.add_column("Agent", style="white")
            avail_table.add_column("Status", style="white")
            for name, is_available in availability:
                if is_available:
                    status = "[green]on PATH[/green]"
                else:
                    status = "[yellow]⚠ missing (not on PATH)[/yellow]"
                avail_table.add_row(name, status)
            content.append(avail_table)
            return content
        except Exception:
            pass
    content.append(Text("Ensure your AI agents are on PATH (e.g., `claude`, `opencode`)"))
    return content


def emit_first_run_welcome(
    console: object,
    results: list[BootstrapResult],
    *,
    agent_registry: _HasListAgents | None = None,
) -> None:
    """Print a structured first-run welcome panel.

    Args:
        console: A rich.console.Console-like object with a .print() method.
        results: Bootstrap results from a bootstrap operation.
        agent_registry: Optional agent registry for availability checking.
    """
    # No-op when everything was skipped (subsequent runs)
    if all(r.action == "skipped" for r in results):
        return

    has_new_or_regenerated = any(r.action in {"created", "regenerated"} for r in results)
    if not has_new_or_regenerated:
        return

    content: list[object] = []

    # Config files table
    global_files: list[str] = []
    local_files: list[str] = []
    for result in results:
        if result.action == "skipped":
            continue
        path_str = str(result.path)
        if ".agent" in path_str or path_str.startswith("."):
            local_files.append(path_str)
        else:
            global_files.append(path_str)

    if global_files or local_files:
        table = Table(show_header=True, header_style="bold cyan", box=None)
        table.add_column("Scope", style="dim")
        table.add_column("File", style="white")
        for f in global_files:
            table.add_row("global", f)
        for f in local_files:
            table.add_row("local", f)
        content.append(table)

    # Agent availability
    content.extend(_build_agent_availability_content(agent_registry))

    # Next steps
    next_steps = Text("\n[bold]Next steps:[/bold]\n", style="cyan")
    next_steps.append("1. Edit [cyan]PROMPT.md[/cyan] with your implementation task\n")
    next_steps.append("2. Install AI agents if missing (e.g., `claude`, `opencode`)\n")
    next_steps.append("3. Run [cyan]ralph[/cyan] to start the pipeline\n")
    next_steps.append("4. Run [cyan]ralph --regenerate-config[/cyan] to reset configs")
    content.append(next_steps)

    panel = Panel(
        Group(*content),  # type: ignore[arg-type]
        title="Ralph first-run setup",
        border_style="cyan",
        padding=(1, 2),
    )
    console.print(panel)  # type: ignore[attr-defined]
