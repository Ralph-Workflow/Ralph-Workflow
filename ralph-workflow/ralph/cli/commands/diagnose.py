"""Diagnose command for Ralph Workflow CLI.

This module implements diagnostic commands to check the
environment and configuration.
"""

from __future__ import annotations

from importlib import import_module
from pathlib import Path
from typing import TYPE_CHECKING, cast

from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from ralph.agents.availability import check_agent_availability
from ralph.agents.registry import AgentRegistry
from ralph.config.loader import load_config
from ralph.display.context import make_display_context
from ralph.git.operations import find_repo_root, is_repo_clean
from ralph.mcp.transport.common import mcp_toml_as_upstreams
from ralph.mcp.upstream.agent_probe import probe_agent_transports
from ralph.mcp.upstream.validation import validate_upstream_mcp_servers
from ralph.onboarding import (
    GETTING_STARTED_DOC,
    INIT_COMMAND,
    RUN_COMMAND,
    starter_prompt_validation_hint,
)
from ralph.policy.loader import (
    load_policy,
    load_policy_for_workspace_scope,
)
from ralph.policy.validation import (
    PolicyValidationError,
    validate_agent_chains_satisfiable,
    validate_recovery_config,
)
from ralph.workspace.scope import WorkspaceScope, resolve_workspace_scope

if TYPE_CHECKING:
    from types import ModuleType

    from ralph.display.context import DisplayContext


def _module_attr(module: ModuleType, attribute: str) -> object:
    namespace = cast("dict[str, object]", module.__dict__)
    return namespace[attribute]


def _load_starter_prompt_sentinel() -> str:
    return cast(
        "str",
        _module_attr(import_module("ralph.cli.commands.init"), "STARTER_PROMPT_SENTINEL"),
    )


def diagnose_command(
    config_path: Path | None = None,
    cli_overrides: dict[str, object] | None = None,
    *,
    display_context: DisplayContext | None = None,
) -> int:
    """Run diagnostics on the Ralph Workflow environment.

    Args:
        config_path: Optional path to config file.
        cli_overrides: CLI flag overrides.
        display_context: Display context for consistent rendering. If None, a default
            context is created using make_display_context().

    Returns:
        Exit code (0 for success, 1 for errors, 2 for validation failures).
    """
    ctx = display_context if display_context is not None else make_display_context()
    console = ctx.console

    title = Text()
    title.append("Ralph Workflow Diagnostics", style="theme.banner.title")
    console.print()
    console.print(title)
    console.print()

    workspace_scope = resolve_workspace_scope()

    config_ok = _check_git_repo(display_context=ctx)
    config_ok &= _check_configuration(config_path, cli_overrides, display_context=ctx)
    agent_missing = check_agents(cli_overrides, display_context=ctx)
    config_ok &= not agent_missing
    config_ok &= _check_mcp_servers(workspace_scope, display_context=ctx)
    config_ok &= _check_workspace_files(display_context=ctx)

    # Pre-flight validation using policy system
    validation_ok = _run_preflight_validation(
        config_path, cli_overrides, workspace_scope, display_context=ctx
    )

    # Build and print next steps
    prompt_path = workspace_scope.root / "PROMPT.md"
    prompt_exists = prompt_path.exists()
    prompt_has_sentinel = False
    if prompt_exists:
        try:
            sentinel = _load_starter_prompt_sentinel()
            prompt_has_sentinel = sentinel in prompt_path.read_text(encoding="utf-8")
        except Exception:
            pass

    next_steps = build_next_steps(
        validation_ok=validation_ok,
        agent_missing=agent_missing,
        prompt_exists=prompt_exists,
        prompt_has_sentinel=prompt_has_sentinel,
    )
    _print_next_steps_panel(next_steps, display_context=ctx)

    console.print()

    if not validation_ok:
        return 2
    if not config_ok:
        return 1
    return 0


def build_next_steps(
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
        steps.append(f"Run `{INIT_COMMAND}` to scaffold PROMPT.md and project config files.")
    elif prompt_has_sentinel:
        steps.append(starter_prompt_validation_hint())

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
        steps.append(f"Run `{RUN_COMMAND}` to start the pipeline.")

    return steps


def _print_next_steps_panel(steps: list[str], *, display_context: DisplayContext) -> None:
    """Print the Next steps panel to the console."""
    c = display_context.console
    content = Text()
    for i, step in enumerate(steps):
        if i > 0:
            content.append("\n")
        content.append(f"  • {step}")
    content.append("\n\n")
    content.append("New to Ralph Workflow? ", style="theme.text.muted")
    content.append(GETTING_STARTED_DOC, style="theme.text.muted")
    content.append(" — step-by-step walkthrough.", style="theme.text.muted")
    c.print(Panel(content, title="Next steps", border_style="theme.phase.planning", padding=(1, 2)))


def _run_preflight_validation(
    config_path: Path | None,
    cli_overrides: dict[str, object] | None,
    workspace_scope: WorkspaceScope,
    *,
    display_context: DisplayContext,
) -> bool:
    """Run pre-flight validation on policy configuration.

    Args:
        config_path: Optional path to config file.
        cli_overrides: CLI flag overrides.
        workspace_scope: Workspace scope.
        display_context: DisplayContext providing the console for output.

    Returns:
        True if validation passes, False otherwise.
    """
    c = display_context.console
    table = Table(title="Pre-flight Validation", show_header=False)
    table.add_column("Check", style="theme.cat.meta")
    table.add_column("Status")

    try:
        # Load UnifiedConfig for agent registry
        config = load_config(config_path, cli_overrides, workspace_scope=workspace_scope)
        registry = AgentRegistry.from_config(config)

        # Determine policy directory
        if config_path is not None:
            policy_dir = config_path.parent
            has_effective_policy_files = any(
                (policy_dir / name).exists()
                for name in (
                    "ralph-workflow.toml",
                    "agents.toml",
                    "pipeline.toml",
                    "artifacts.toml",
                )
            )
        else:
            policy_dir = workspace_scope.resolve_agent_file("pipeline.toml").parent
            has_effective_policy_files = any(
                workspace_scope.resolve_agent_file(name).exists()
                for name in (
                    "ralph-workflow.toml",
                    "agents.toml",
                    "pipeline.toml",
                    "artifacts.toml",
                )
            )
        if not has_effective_policy_files:
            table.add_row(
                "Pre-flight",
                Text(
                    "Skipped: project is not initialized yet (run `ralph --init`)",
                    style="theme.status.warning",
                ),
            )
            c.print(table)
            return True

        # Load PolicyBundle for validation
        bundle = (
            load_policy(policy_dir, config=config)
            if config_path is not None
            else load_policy_for_workspace_scope(workspace_scope, config=config)
        )

        # Run validators
        validate_agent_chains_satisfiable(bundle, registry)
        validate_recovery_config(bundle)

        table.add_row("Agent chains", Text("Satisfiable", style="theme.status.success"))
        table.add_row("Recovery config", Text("Valid", style="theme.status.success"))
        c.print(table)
        return True

    except PolicyValidationError as e:
        table.add_row("Policy validation", _status_text("Failed", e.message, "theme.status.error"))
        c.print(table)
        return False
    except Exception as e:
        table.add_row("Pre-flight", _status_text("Error", str(e), "theme.status.error"))
        c.print(table)
        return False


def _check_git_repo(*, display_context: DisplayContext) -> bool:
    """Check git repository status.

    Args:
        display_context: DisplayContext providing the console for output.

    Returns:
        True if check passed, False otherwise.
    """
    c = display_context.console
    table = Table(title="Git Repository", show_header=False)
    table.add_column("Check", style="theme.cat.meta")
    table.add_column("Status")

    try:
        repo_root = find_repo_root()
        table.add_row("Repository root", str(repo_root))
    except Exception as e:
        table.add_row("Repository", _status_text("Error", str(e), "theme.status.error"))
        c.print(table)
        return False

    try:
        clean = is_repo_clean(repo_root)
        if clean:
            table.add_row("Working tree", Text("Clean", style="theme.status.success"))
        else:
            table.add_row(
                "Working tree", Text("Has uncommitted changes", style="theme.status.warning")
            )
    except Exception as e:
        table.add_row("Working tree", _status_text("Error", str(e), "theme.status.error"))

    c.print(table)
    return True


def _check_configuration(
    config_path: Path | None,
    cli_overrides: dict[str, object] | None,
    *,
    display_context: DisplayContext,
) -> bool:
    """Check configuration validity.

    Args:
        config_path: Optional path to config file.
        cli_overrides: CLI flag overrides.
        display_context: DisplayContext providing the console for output.

    Returns:
        True if check passed, False otherwise.
    """
    c = display_context.console
    table = Table(title="Configuration", show_header=False)
    table.add_column("Check", style="theme.cat.meta")
    table.add_column("Status")

    try:
        workspace_scope = None if config_path is not None else resolve_workspace_scope()
        config = load_config(config_path, cli_overrides, workspace_scope=workspace_scope)
        table.add_row("Config loaded", Text("Success", style="theme.status.success"))
        table.add_row("Developer iters", str(config.general.developer_iters))
        table.add_row("Checkpoint enabled", str(config.general.workflow.checkpoint_enabled))
    except Exception as e:
        table.add_row("Config loaded", _status_text("Error", str(e), "theme.status.error"))
        c.print(table)
        return False

    c.print(table)
    return True


def check_agents(
    cli_overrides: dict[str, object] | None,
    *,
    display_context: DisplayContext,
) -> bool:
    """Check agent availability and return True if any agent is missing from PATH.

    Args:
        cli_overrides: CLI flag overrides.
        display_context: DisplayContext providing the console for output.

    Returns:
        True if at least one agent is missing from PATH, False otherwise.
    """
    c = display_context.console
    table = Table(title="Agents")
    table.add_column("Agent", style="theme.cat.meta")
    table.add_column("Config")
    table.add_column("PATH")

    any_missing = False
    try:
        config = load_config(None, cli_overrides, workspace_scope=resolve_workspace_scope())
        registry = AgentRegistry.from_config(config)
        agent_names = registry.list_agents()
        if not agent_names:
            table.add_row("(none)", Text("No agents configured", style="theme.status.warning"), "-")
        else:
            availability = check_agent_availability(registry)
            path_by_name: dict[str, Text] = {}
            for name, status in availability:
                if status == "available":
                    path_by_name[name] = Text("on PATH", style="theme.status.success")
                else:
                    path_by_name[name] = Text("missing", style="theme.status.warning")
                    any_missing = True
            for name in agent_names:
                agent = registry.get(name)
                cmd = agent.cmd if agent else ""
                path_status = path_by_name.get(name, Text("missing", style="theme.status.warning"))
                config_cell = _status_text("Configured", cmd, "theme.status.success")
                table.add_row(name, config_cell, path_status)
    except Exception as e:
        table.add_row("Agents", _status_text("Error", str(e), "theme.status.error"), "-")
        c.print(table)
        return True

    c.print(table)
    return any_missing


def _check_mcp_servers(
    workspace_scope: WorkspaceScope,
    *,
    display_context: DisplayContext,
) -> bool:
    """Render custom MCP server health and per-agent transport compatibility.

    Args:
        workspace_scope: Workspace scope.
        display_context: DisplayContext providing the console for output.

    Returns:
        True if check passed, False otherwise.
    """
    c = display_context.console

    server_table = Table(title="Custom MCP Servers")
    server_table.add_column("Server", style="theme.cat.meta")
    server_table.add_column("Transport")
    server_table.add_column("Status")
    server_table.add_column("Tools")
    server_table.add_column("Detail")

    upstreams = mcp_toml_as_upstreams(workspace_scope.root)
    if not upstreams:
        server_table.add_row(
            "(none)",
            "-",
            Text("No custom MCP servers configured", style="theme.status.warning"),
            "-",
            "-",
        )
        c.print(server_table)
        return True

    try:
        report = validate_upstream_mcp_servers(upstreams, strict=False)
    except Exception as exc:
        server_table.add_row(
            "(validator)",
            "-",
            _status_text("Error", str(exc), "theme.status.error"),
            "-",
            "-",
        )
        c.print(server_table)
        return False

    for entry in report.servers:
        status = (
            Text("ok", style="theme.status.success")
            if entry.ok
            else Text("failed", style="theme.status.error")
        )
        detail = entry.error or ""
        if entry.secret_keys:
            keys = ",".join(entry.secret_keys)
            detail = f"{detail} (env: {keys})" if detail else f"env: {keys}"
        server_table.add_row(
            entry.name,
            entry.transport,
            status,
            str(entry.tool_count),
            detail or "-",
        )

    c.print(server_table)

    healthy_names = {r.name for r in report.servers if r.ok}
    healthy_servers = tuple(s for s in upstreams if s.name in healthy_names)
    if not healthy_servers:
        return True

    probe_table = Table(title="Agent Transport Compatibility")
    probe_table.add_column("Server", style="theme.cat.meta")
    probe_table.add_column("Claude")
    probe_table.add_column("Codex")
    probe_table.add_column("OpenCode")

    probes = probe_agent_transports(healthy_servers, workspace_path=workspace_scope.root)
    by_server: dict[str, dict[str, Text]] = {}
    for probe in probes:
        if probe.note and probe.ok:
            cell = Text("-", style="theme.status.warning")
        elif probe.ok:
            cell = Text("✓", style="theme.status.success")
        else:
            cell = Text("✗", style="theme.status.error")
        by_server.setdefault(probe.server_name, {})[probe.transport.value] = cell

    for server in healthy_servers:
        cells = by_server.get(server.name, {})
        probe_table.add_row(
            server.name,
            cells.get("claude", Text("-")),
            cells.get("codex", Text("-")),
            cells.get("opencode", Text("-")),
        )

    c.print(probe_table)
    return True


def _check_workspace_files(*, display_context: DisplayContext) -> bool:
    """Check workspace files.

    Args:
        display_context: DisplayContext providing the console for output.

    Returns:
        True if check passed, False otherwise.
    """
    c = display_context.console
    table = Table(title="Workspace Files", show_header=False)
    table.add_column("File", style="theme.cat.meta")
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
            table.add_row(
                file_label, _status_text("Exists", f"{size} bytes", "theme.status.success")
            )
        else:
            table.add_row(file_label, Text("Not found", style="theme.status.warning"))

    c.print(table)
    return True


def _status_text(label: str, detail: str, style: str) -> Text:
    text = Text()
    text.append(f"{label}:", style=style)
    text.append(" ")
    text.append(detail)
    return text


check_git_repo = _check_git_repo
check_configuration = _check_configuration
check_mcp_servers = _check_mcp_servers
check_workspace_files = _check_workspace_files
