"""First-run welcome banner and agent availability helper."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from rich.console import Group, RenderableType
from rich.panel import Panel
from rich.text import Text

from ralph.agents.availability import HasListAgents, check_agent_availability
from ralph.banner import SupportsPrint, show_banner
from ralph.display.context import DisplayContext
from ralph.onboarding import getting_started_pointer_sentence, welcome_panel_next_steps

if TYPE_CHECKING:
    from ralph.config.bootstrap import BootstrapResult
    from ralph.display.context import DisplayContext

_KNOWN_AGENT_INSTALL_URLS: dict[str, str] = {
    "claude": "https://docs.claude.com/claude-code",
    "opencode": "https://opencode.ai",
}


def _build_agent_availability_content(
    agent_registry: HasListAgents | None,
) -> list[RenderableType]:
    """Build agent availability content or generic PATH message."""
    content: list[RenderableType] = []
    if agent_registry is not None:
        try:
            availability = check_agent_availability(agent_registry)
            avail_lines: list[Text] = []
            for registry_name, status in availability:
                agent = agent_registry.get(registry_name)
                label = (
                    (agent.display_name or registry_name) if agent is not None else registry_name
                )
                if status == "available":
                    t = Text(f"  • {label}: ")
                    t.append("on PATH", style="theme.status.success")
                    avail_lines.append(t)
                elif status == "missing_on_path":
                    install_url = _KNOWN_AGENT_INSTALL_URLS.get(registry_name.lower())
                    t = Text(f"  • {label}: ")
                    t.append("⚠ missing (not on PATH)", style="theme.status.warning")
                    if install_url:
                        t.append(f" install: {install_url}", style="theme.text.muted")
                    avail_lines.append(t)
                else:  # no_cmd
                    t = Text(f"  • {label}: ")
                    t.append("⚠ missing (not on PATH)", style="theme.status.warning")
                    avail_lines.append(t)
            if avail_lines:
                content.append(Text("Detected agents:", style="theme.banner.title"))
                content.extend(avail_lines)
                return content
        except Exception:
            pass
    content.append(Text("Ensure your AI agents are on PATH (e.g., `claude`, `opencode`, `agy`)"))
    return content


def _build_regenerate_summary(results: list[BootstrapResult]) -> Text | None:
    """Build summary text for regenerate operation showing backup info."""
    regenerated = [r for r in results if r.action == "regenerated"]
    if not regenerated:
        return None
    backup_count = sum(1 for r in regenerated if r.backup is not None)
    text = Text(f"Regenerated {len(regenerated)} config file(s)")
    if backup_count > 0:
        text.append(" (")
        text.append(
            f"{backup_count} backup(s) saved with .bak suffix",
            style="theme.status.warning",
        )
        text.append(")")
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


def _append_file_section(content: list[RenderableType], heading: str, files: list[str]) -> None:
    """Append a headed bullet list of config files when present."""
    if not files:
        return
    content.append(Text(heading, style="theme.banner.title"))
    content.extend(Text(f"  • {name}") for name in files)


def _build_next_steps_text() -> Text:
    """Build the welcome panel next-steps block."""
    next_steps = Text("Next steps:\n", style="theme.banner.title")
    lines = welcome_panel_next_steps()
    for index, line in enumerate(lines, start=1):
        next_steps.append(f"  {index}. {line}")
        if index < len(lines):
            next_steps.append("\n")
    return next_steps


def emit_first_run_welcome(
    console: object,
    results: list[BootstrapResult],
    *,
    agent_registry: HasListAgents | None = None,
    is_regenerate: bool = False,
    display_context: DisplayContext,
) -> None:
    """Print a structured first-run welcome panel.

    Args:
        console: A rich.console.Console-like object with a .print() method.
        results: Bootstrap results from a bootstrap operation.
        agent_registry: Optional agent registry for availability checking.
        is_regenerate: Whether this is a regenerate (--regenerate-config) operation.
        display_context: Display context for adaptive layout (required).
    """
    if all(r.action == "skipped" for r in results):
        return

    has_new_or_regenerated = any(r.action in {"created", "regenerated"} for r in results)
    if not has_new_or_regenerated:
        return

    rich_console = cast("SupportsPrint", console)
    show_banner(display_context=display_context, console=rich_console)

    content: list[RenderableType] = []

    intro = Text("Ralph Workflow orchestrates AI coding agents through a ")
    intro.append("planning → development loop", style="theme.phase.planning")
    intro.append(" driven by your PROMPT.md.")
    content.append(intro)

    docs_line1 = Text(getting_started_pointer_sentence(), style="theme.text.muted")
    content.append(docs_line1)

    docs_line2 = Text("Offline docs: ", style="theme.text.muted")
    docs_line2.append("python -m pydoc ralph", style="theme.cat.meta")
    docs_line2.append(" · run ", style="theme.text.muted")
    docs_line2.append("make serve-docs", style="theme.cat.meta")
    docs_line2.append(
        " from ralph-workflow/ for the full HTML reference.",
        style="theme.text.muted",
    )
    content.append(docs_line2)

    content.append(Text())  # blank line

    if is_regenerate:
        summary = _build_regenerate_summary(results)
        if summary:
            content.append(summary)
            content.append(Text())  # blank line

    if not is_regenerate:
        content.extend(_build_agent_availability_content(agent_registry))

    global_files, local_files = _partition_config_files(results)
    _append_file_section(content, "Global config files:", global_files)
    _append_file_section(content, "Local config files:", local_files)

    content.append(_build_next_steps_text())

    panel = Panel(
        Group(*content),
        title="Ralph Workflow first-run setup",
        border_style="theme.banner.border",
        padding=(1, 2),
    )
    rich_console.print(panel)
