"""First-run welcome banner and agent availability helper."""

from __future__ import annotations

import shutil
from typing import TYPE_CHECKING, Literal, Protocol, runtime_checkable

from rich.console import Group
from rich.panel import Panel
from rich.text import Text

from ralph.banner import show_banner

if TYPE_CHECKING:
    from ralph.config.bootstrap import BootstrapResult

_KNOWN_AGENT_INSTALL_URLS: dict[str, str] = {
    "claude": "https://docs.claude.com/claude-code",
    "opencode": "https://opencode.ai",
}

_AgentStatus = Literal["available", "missing_on_path", "no_cmd"]


class _AgentEntry(Protocol):
    """Minimal agent config interface for availability checks."""

    cmd: str
    display_name: str | None


@runtime_checkable
class _HasListAgents(Protocol):
    """Protocol for agent registries used in availability checks."""

    def list_agents(self) -> list[str]:
        ...

    def get(self, name: str) -> _AgentEntry | None:
        ...


def _check_agent_availability(
    registry: _HasListAgents,
) -> list[tuple[str, _AgentStatus]]:
    """Check which agents are available on PATH.

    Args:
        registry: Object implementing list_agents() and get(name) for agent resolution.

    Returns:
        List of (display_name, status) tuples.
    """
    results: list[tuple[str, _AgentStatus]] = []
    for name in registry.list_agents():
        agent = registry.get(name)
        if agent is None:
            continue
        cmd = agent.cmd
        if not cmd:
            results.append((agent.display_name or name, "no_cmd"))
            continue
        first_word = cmd.split(maxsplit=1)[0]
        display = agent.display_name or first_word
        status: _AgentStatus = (
            "available" if shutil.which(first_word) is not None else "missing_on_path"
        )
        results.append((display, status))
    return results


def _build_agent_availability_content(
    agent_registry: _HasListAgents | None,
) -> list[object]:
    """Build agent availability content or generic PATH message."""
    content: list[object] = []
    if agent_registry is not None:
        try:
            availability = _check_agent_availability(agent_registry)
            avail_lines: list[Text] = []
            for name, status in availability:
                if status == "available":
                    avail_lines.append(
                        Text.from_markup(f"  • {name}: [green]on PATH[/green]")
                    )
                elif status == "missing_on_path":
                    install_url = _KNOWN_AGENT_INSTALL_URLS.get(name.lower())
                    if install_url:
                        avail_lines.append(
                            Text.from_markup(
                                f"  • {name}: "
                                "[yellow]⚠ missing (not on PATH)[/yellow] "
                                f"[dim]install: {install_url}[/dim]"
                            )
                        )
                    else:
                        avail_lines.append(
                            Text.from_markup(
                                f"  • {name}: "
                                "[yellow]⚠ missing (not on PATH)[/yellow]"
                            )
                        )
                else:  # no_cmd
                    avail_lines.append(
                        Text.from_markup(
                            f"  • {name}: [yellow]⚠ missing (not on PATH)[/yellow]"
                        )
                    )
            if avail_lines:
                content.append(Text.from_markup("[bold cyan]Detected agents:[/bold cyan]"))
                content.extend(avail_lines)
                return content
        except Exception:
            pass
    content.append(Text("Ensure your AI agents are on PATH (e.g., `claude`, `opencode`)"))
    return content


def _build_regenerate_summary(results: list[BootstrapResult]) -> Text | None:
    """Build summary text for regenerate operation showing backup info."""
    regenerated = [r for r in results if r.action == "regenerated"]
    if not regenerated:
        return None
    backup_count = sum(1 for r in regenerated if r.backup is not None)
    text = Text.from_markup(f"Regenerated {len(regenerated)} config file(s)")
    if backup_count > 0:
        text.append_text(
            Text.from_markup(
                f" ([yellow]{backup_count} backup(s) saved with .bak suffix[/yellow])"
            )
        )
    return text


def _partition_config_files(results: list[BootstrapResult]) -> tuple[list[str], list[str]]:
    """Split created config file names into global and local display groups."""
    global_files: list[str] = []
    local_files: list[str] = []
    for result in results:
        if result.action == "skipped":
            continue
        path_str = str(result.path)
        filename = result.path.name
        if ".agent" in path_str or path_str.startswith("."):
            local_files.append(filename)
        else:
            global_files.append(filename)
    return global_files, local_files


def _append_file_section(content: list[object], heading: str, files: list[str]) -> None:
    """Append a headed bullet list of config files when present."""
    if not files:
        return
    content.append(Text.from_markup(heading))
    content.extend(Text(f"  • {name}") for name in files)


def emit_first_run_welcome(
    console: object,
    results: list[BootstrapResult],
    *,
    agent_registry: _HasListAgents | None = None,
    is_regenerate: bool = False,
) -> None:
    """Print a structured first-run welcome panel.

    Args:
        console: A rich.console.Console-like object with a .print() method.
        results: Bootstrap results from a bootstrap operation.
        agent_registry: Optional agent registry for availability checking.
        is_regenerate: Whether this is a regenerate (--regenerate-config) operation.
    """
    # No-op when everything was skipped (subsequent runs)
    if all(r.action == "skipped" for r in results):
        return

    has_new_or_regenerated = any(r.action in {"created", "regenerated"} for r in results)
    if not has_new_or_regenerated:
        return

    show_banner(console=console)  # type: ignore[arg-type]

    content: list[object] = []

    # Elevator pitch and docs pointer for new users
    content.append(
        Text.from_markup(
            "Ralph Workflow orchestrates AI coding agents through a "
            "[cyan]planning → development → review → fix[/cyan] loop "
            "driven by your PROMPT.md."
        )
    )
    content.append(
        Text.from_markup(
            "[dim]Learn more: [cyan]python -m pydoc ralph[/cyan] · "
            "run [cyan]make serve-docs[/cyan] from ralph-workflow/ "
            "for the full HTML reference.[/dim]"
        )
    )
    content.append(Text())  # blank line

    # For regenerate, show summary line first
    if is_regenerate:
        summary = _build_regenerate_summary(results)
        if summary:
            content.append(summary)
            content.append(Text())  # blank line

    # Agent availability (shown before config file lists; not shown during regenerate)
    if not is_regenerate:
        content.extend(_build_agent_availability_content(agent_registry))

    global_files, local_files = _partition_config_files(results)
    _append_file_section(content, "[bold cyan]Global config files:[/bold cyan]", global_files)
    _append_file_section(content, "[bold cyan]Local config files:[/bold cyan]", local_files)

    # Next steps
    next_steps = Text.from_markup(
        "[bold cyan]Next steps:[/bold cyan]\n"
        "  1. Edit [cyan]PROMPT.md[/cyan] with your implementation task\n"
        "  2. Install AI agents if missing (e.g., `claude`, `opencode`)\n"
        "  3. (Optional) Run [cyan]ralph --diagnose[/cyan] to verify agents,"
        " MCP servers, and config\n"
        "  4. Run [cyan]ralph[/cyan] to start the pipeline\n"
        "  5. Run [cyan]ralph --regenerate-config[/cyan] to reset configs"
    )
    content.append(next_steps)

    panel = Panel(
        Group(*content),  # type: ignore[arg-type]
        title="Ralph Workflow first-run setup",
        border_style="cyan",
        padding=(1, 2),
    )
    console.print(panel)  # type: ignore[attr-defined]
