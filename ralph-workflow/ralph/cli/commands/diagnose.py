"""Diagnose command for Ralph Workflow CLI.

This module implements diagnostic commands to check the
environment and configuration.
"""

from __future__ import annotations

from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from ralph.agents.availability import check_agent_availability
from ralph.agents.registry import AgentRegistry
from ralph.config.loader import load_config
from ralph.git.operations import find_repo_root, is_repo_clean
from ralph.policy.loader import PolicyValidationError, load_policy
from ralph.policy.validation import (
    validate_agent_chains_satisfiable,
    validate_recovery_config,
)
from ralph.workspace.scope import WorkspaceScope, resolve_workspace_scope

console = Console()


def diagnose_command(
    config_path: Path | None = None,
    cli_overrides: dict[str, object] | None = None,
) -> int:
    """Run diagnostics on the Ralph Workflow environment.

    Args:
        config_path: Optional path to config file.
        cli_overrides: CLI flag overrides.

    Returns:
        Exit code (0 for success, 1 for errors, 2 for validation failures).
    """
    console.print("\n[cyan bold]Ralph Workflow Diagnostics[/cyan bold]\n")

    workspace_scope = resolve_workspace_scope()

    config_ok = _check_git_repo()
    config_ok &= _check_configuration(config_path, cli_overrides)
    agent_missing = _check_agents_returning_missing(cli_overrides)
    config_ok &= not agent_missing
    config_ok &= _check_mcp_servers(workspace_scope)
    config_ok &= _check_workspace_files()

    # Pre-flight validation using policy system
    validation_ok = _run_preflight_validation(config_path, cli_overrides, workspace_scope)

    # Build and print next steps
    prompt_path = workspace_scope.root / "PROMPT.md"
    prompt_exists = prompt_path.exists()
    prompt_has_sentinel = False
    if prompt_exists:
        try:
            from ralph.cli.commands.init import STARTER_PROMPT_SENTINEL  # noqa: PLC0415
            prompt_has_sentinel = STARTER_PROMPT_SENTINEL in prompt_path.read_text(encoding="utf-8")
        except Exception:
            pass

    next_steps = _build_next_steps(
        validation_ok=validation_ok,
        agent_missing=agent_missing,
        prompt_exists=prompt_exists,
        prompt_has_sentinel=prompt_has_sentinel,
    )
    _print_next_steps_panel(next_steps)

    console.print()

    if not validation_ok:
        return 2
    if not config_ok:
        return 1
    return 0


def _build_next_steps(
    *,
    validation_ok: bool,
    agent_missing: bool,
    prompt_exists: bool,
    prompt_has_sentinel: bool,
) -> list[str]:
    """Build the list of remediation steps based on current diagnostic state.

    Args:
        validation_ok: Whether pre-flight validation passed.
        agent_missing: Whether any configured agent is missing from PATH.
        prompt_exists: Whether PROMPT.md exists in the workspace.
        prompt_has_sentinel: Whether PROMPT.md still contains the starter sentinel.

    Returns:
        List of human-readable remediation lines.
    """
    steps: list[str] = []

    if not prompt_exists:
        steps.append("Run `ralph --init` to scaffold PROMPT.md and project config files.")
    elif prompt_has_sentinel:
        steps.append(
            "Edit PROMPT.md to remove the `<!-- ralph:starter-prompt ... -->` marker "
            "and describe your task."
        )

    if agent_missing:
        steps.append(
            "Install at least one supported agent: "
            "Claude Code (https://docs.claude.com/claude-code) "
            "or OpenCode (https://opencode.ai)."
        )

    if not validation_ok:
        steps.append(
            "Pre-flight validation failed: see the Pre-flight Validation table above. "
            "Fix policy errors with `ralph --regenerate-config` if config files were edited."
        )

    if not steps:
        steps.append("Run `ralph` to start the pipeline.")

    return steps


def _print_next_steps_panel(steps: list[str]) -> None:
    """Print the Next steps panel to the console."""
    content = Text()
    for i, step in enumerate(steps):
        if i > 0:
            content.append("\n")
        content.append(f"  • {step}")
    content.append("\n\n")
    content.append("New to Ralph Workflow? ", style="dim")
    content.append("docs/sphinx/getting-started.md", style="dim cyan")
    content.append(" — step-by-step walkthrough.", style="dim")
    console.print(Panel(content, title="Next steps", border_style="cyan", padding=(1, 2)))


def _run_preflight_validation(
    config_path: Path | None,
    cli_overrides: dict[str, object] | None,
    workspace_scope: WorkspaceScope,
) -> bool:
    """Run pre-flight validation on policy configuration.

    Args:
        config_path: Optional path to config file.
        cli_overrides: CLI flag overrides.
        workspace_scope: Workspace scope.

    Returns:
        True if validation passes, False otherwise.
    """
    table = Table(title="Pre-flight Validation", show_header=False)
    table.add_column("Check", style="cyan")
    table.add_column("Status")

    try:
        # Load UnifiedConfig for agent registry
        config = load_config(config_path, cli_overrides, workspace_scope=workspace_scope)
        registry = AgentRegistry.from_config(config)

        # Determine policy directory
        if config_path is not None:
            policy_dir = config_path.parent
        else:
            policy_dir = workspace_scope.root / ".agent"

        # Load PolicyBundle for validation
        bundle = load_policy(policy_dir, config=config)

        # Run validators
        validate_agent_chains_satisfiable(bundle, registry)
        validate_recovery_config(bundle)

        table.add_row("Agent chains", "[green]Satisfiable[/green]")
        table.add_row("Recovery config", "[green]Valid[/green]")
        console.print(table)
        return True

    except PolicyValidationError as e:
        table.add_row("Policy validation", _status_text("Failed", e.message, "red"))
        console.print(table)
        return False
    except Exception as e:
        table.add_row("Pre-flight", _status_text("Error", str(e), "red"))
        console.print(table)
        return False


def _check_git_repo() -> bool:
    """Check git repository status.

    Returns:
        True if check passed, False otherwise.
    """
    table = Table(title="Git Repository", show_header=False)
    table.add_column("Check", style="cyan")
    table.add_column("Status")

    try:
        repo_root = find_repo_root()
        table.add_row("Repository root", str(repo_root))
    except Exception as e:
        table.add_row("Repository", _status_text("Error", str(e), "red"))
        console.print(table)
        return False

    try:
        clean = is_repo_clean(repo_root)
        if clean:
            table.add_row("Working tree", "[green]Clean[/green]")
        else:
            table.add_row("Working tree", "[yellow]Has uncommitted changes[/yellow]")
    except Exception as e:
        table.add_row("Working tree", _status_text("Error", str(e), "red"))

    console.print(table)
    return True


def _check_configuration(
    config_path: Path | None,
    cli_overrides: dict[str, object] | None,
) -> bool:
    """Check configuration validity.

    Returns:
        True if check passed, False otherwise.
    """
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
        return False

    console.print(table)
    return True


def _check_agents(cli_overrides: dict[str, object] | None) -> bool:
    """Check agent availability, including PATH presence.

    Returns:
        True if check passed, False otherwise.
    """
    return not _check_agents_returning_missing(cli_overrides)


def _check_agents_returning_missing(cli_overrides: dict[str, object] | None) -> bool:
    """Check agent availability and return True if any agent is missing from PATH.

    Returns:
        True if at least one agent is missing from PATH, False otherwise.
    """
    table = Table(title="Agents")
    table.add_column("Agent", style="cyan")
    table.add_column("Config")
    table.add_column("PATH")

    any_missing = False
    try:
        config = load_config(None, cli_overrides, workspace_scope=resolve_workspace_scope())
        registry = AgentRegistry.from_config(config)
        agent_names = registry.list_agents()
        if not agent_names:
            table.add_row("(none)", "[yellow]No agents configured[/yellow]", "-")
        else:
            availability = check_agent_availability(registry)
            path_by_name: dict[str, str] = {}
            for name, status in availability:
                if status == "available":
                    path_by_name[name] = "[green]on PATH[/green]"
                else:
                    path_by_name[name] = "[yellow]missing[/yellow]"
                    any_missing = True
            for name in agent_names:
                agent = registry.get(name)
                cmd = agent.cmd if agent else ""
                path_status = path_by_name.get(name, "[yellow]missing[/yellow]")
                config_cell = _status_text("Configured", cmd, "green")
                table.add_row(name, config_cell, path_status)
    except Exception as e:
        table.add_row("Agents", _status_text("Error", str(e), "red"), "-")
        console.print(table)
        return True

    console.print(table)
    return any_missing


def _check_mcp_servers(workspace_scope: WorkspaceScope) -> bool:
    """Render custom MCP server health and per-agent transport compatibility.

    Returns:
        True if check passed, False otherwise.
    """
    from ralph.mcp.transport.common import mcp_toml_as_upstreams  # noqa: PLC0415
    from ralph.mcp.upstream.agent_probe import probe_agent_transports  # noqa: PLC0415
    from ralph.mcp.upstream.validation import validate_upstream_mcp_servers  # noqa: PLC0415

    server_table = Table(title="Custom MCP Servers")
    server_table.add_column("Server", style="cyan")
    server_table.add_column("Transport")
    server_table.add_column("Status")
    server_table.add_column("Tools")
    server_table.add_column("Detail")

    upstreams = mcp_toml_as_upstreams(workspace_scope.root)
    if not upstreams:
        server_table.add_row(
            "(none)",
            "-",
            "[yellow]No custom MCP servers configured[/yellow]",
            "-",
            "-",
        )
        console.print(server_table)
        return True

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
        return False

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
        return True

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
    return True


def _check_workspace_files() -> bool:
    """Check workspace files.

    Returns:
        True if check passed, False otherwise.
    """
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
    return True


def _status_text(label: str, detail: str, style: str) -> Text:
    text = Text()
    text.append(f"{label}:", style=style)
    text.append(" ")
    text.append(detail)
    return text
