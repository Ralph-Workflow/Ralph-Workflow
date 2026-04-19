"""Diagnose command for Ralph CLI.

This module implements diagnostic commands to check the
environment and configuration.
"""

from __future__ import annotations

from pathlib import Path

from rich.console import Console
from rich.table import Table
from rich.text import Text

from ralph.config.loader import load_config
from ralph.git.operations import find_repo_root, is_repo_clean
from ralph.workspace.scope import WorkspaceScope, resolve_workspace_scope

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

    workspace_scope = resolve_workspace_scope()

    _check_git_repo()
    _check_configuration(config_path, cli_overrides)
    _check_agents(cli_overrides)
    _check_mcp_servers(workspace_scope)
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
        table.add_row("Repository", _status_text("Error", str(e), "red"))
        console.print(table)
        return

    try:
        clean = is_repo_clean(repo_root)
        if clean:
            table.add_row("Working tree", "[green]Clean[/green]")
        else:
            table.add_row("Working tree", "[yellow]Has uncommitted changes[/yellow]")
    except Exception as e:
        table.add_row("Working tree", _status_text("Error", str(e), "red"))

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
        workspace_scope = None if config_path is not None else resolve_workspace_scope()
        config = load_config(config_path, cli_overrides, workspace_scope=workspace_scope)
        table.add_row("Config loaded", "[green]Success[/green]")
        table.add_row("Developer iters", str(config.general.developer_iters))
        table.add_row("Reviewer reviews", str(config.general.reviewer_reviews))
        table.add_row("Review depth", config.general.review_depth.value)
        table.add_row("Checkpoint enabled", str(config.general.workflow.checkpoint_enabled))
    except Exception as e:
        table.add_row("Config loaded", _status_text("Error", str(e), "red"))

    console.print(table)


def _check_agents(cli_overrides: dict[str, object] | None) -> None:
    """Check agent availability."""
    table = Table(title="Agents", show_header=False)
    table.add_column("Agent", style="cyan")
    table.add_column("Status")

    try:
        config = load_config(None, cli_overrides, workspace_scope=resolve_workspace_scope())
        if not config.agents:
            table.add_row("No agents", "[yellow]No agents configured[/yellow]")
        else:
            for name, agent_config in config.agents.items():
                table.add_row(name, _status_text("Configured", agent_config.cmd, "green"))
    except Exception as e:
        table.add_row("Agents", _status_text("Error", str(e), "red"))

    console.print(table)


def _check_mcp_servers(workspace_scope: WorkspaceScope) -> None:
    """Render custom MCP server health and per-agent transport compatibility."""
    from ralph.agents.transport_emit import _mcp_toml_as_upstreams  # noqa: PLC0415
    from ralph.mcp.agent_transport_probe import probe_agent_transports  # noqa: PLC0415
    from ralph.mcp.upstream_validation import validate_upstream_mcp_servers  # noqa: PLC0415

    server_table = Table(title="Custom MCP Servers")
    server_table.add_column("Server", style="cyan")
    server_table.add_column("Transport")
    server_table.add_column("Status")
    server_table.add_column("Tools")
    server_table.add_column("Detail")

    upstreams = _mcp_toml_as_upstreams(workspace_scope.root)
    if not upstreams:
        server_table.add_row(
            "(none)",
            "-",
            "[yellow]No custom MCP servers configured[/yellow]",
            "-",
            "-",
        )
        console.print(server_table)
        return

    try:
        report = validate_upstream_mcp_servers(upstreams, strict=False)
    except Exception as exc:
        server_table.add_row(
            "(validator)",
            "-",
            _status_text("Error", str(exc), "red"),
            "-",
            "-",
        )
        console.print(server_table)
        return

    for entry in report.servers:
        status = "[green]ok[/green]" if entry.ok else "[red]failed[/red]"
        detail = entry.error or ""
        if entry.secret_keys:
            keys = ",".join(entry.secret_keys)
            detail = f"{detail} [dim](env: {keys})[/dim]" if detail else f"[dim]env: {keys}[/dim]"
        server_table.add_row(
            entry.name,
            entry.transport,
            status,
            str(entry.tool_count),
            detail or "-",
        )

    console.print(server_table)

    healthy_names = {r.name for r in report.servers if r.ok}
    healthy_servers = tuple(s for s in upstreams if s.name in healthy_names)
    if not healthy_servers:
        return

    probe_table = Table(title="Agent Transport Compatibility")
    probe_table.add_column("Server", style="cyan")
    probe_table.add_column("Claude")
    probe_table.add_column("Codex")
    probe_table.add_column("OpenCode")

    probes = probe_agent_transports(healthy_servers, workspace_path=workspace_scope.root)
    by_server: dict[str, dict[str, str]] = {}
    for probe in probes:
        if probe.note and probe.ok:
            cell = "[yellow]-[/yellow]"
        elif probe.ok:
            cell = "[green]✓[/green]"
        else:
            cell = "[red]✗[/red]"
        by_server.setdefault(probe.server_name, {})[probe.transport.value] = cell

    for server in healthy_servers:
        cells = by_server.get(server.name, {})
        probe_table.add_row(
            server.name,
            cells.get("claude", "-"),
            cells.get("codex", "-"),
            cells.get("opencode", "-"),
        )

    console.print(probe_table)


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
        file_label = Text()
        file_label.append(f"{file_path} ({description})")
        if path.exists():
            size = path.stat().st_size
            table.add_row(file_label, _status_text("Exists", f"{size} bytes", "green"))
        else:
            table.add_row(file_label, Text("Not found", style="yellow"))

    console.print(table)


def _status_text(label: str, detail: str, style: str) -> Text:
    text = Text()
    text.append(f"{label}:", style=style)
    text.append(" ")
    text.append(detail)
    return text
