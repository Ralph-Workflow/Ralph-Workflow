"""Diagnose command for Ralph Workflow CLI.

This module implements diagnostic commands to check the
environment and configuration.
"""

from __future__ import annotations

from importlib import import_module
from pathlib import Path
from typing import TYPE_CHECKING, cast

from rich.text import Text

from ralph.agents.availability import check_agent_availability
from ralph.agents.registry import AgentRegistry
from ralph.config.loader import load_config
from ralph.diagnostics.fs_health import FsHealth
from ralph.display.context import make_display_context
from ralph.display.parallel_display import resolve_active_display
from ralph.git.operations import find_repo_root, is_repo_clean
from ralph.mcp.session_plan import resolve_effective_session_mcp_plan
from ralph.mcp.transport.agy import load_existing_agy_upstream_servers
from ralph.mcp.transport.claude import load_existing_claude_upstream_servers
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
from ralph.pro_support.prompt import resolve_effective_prompt_path
from ralph.skills._baseline_catalog import STATIC_BUILTIN_CAPABILITIES
from ralph.skills._capability_status import CapabilityStatus
from ralph.skills._state_store import load_capability_state
from ralph.skills.manager import SkillManager
from ralph.workspace.scope import WorkspaceScope, resolve_workspace_scope

if TYPE_CHECKING:
    from types import ModuleType

    from ralph.display.context import DisplayContext
    from ralph.mcp.upstream.config import UpstreamMcpServer


def _module_attr(module: ModuleType, attribute: str) -> object:
    namespace = cast("dict[str, object]", module.__dict__)
    return namespace[attribute]


def _load_starter_prompt_sentinel() -> str:
    return cast(
        "str",
        _module_attr(import_module("ralph.cli.commands.init"), "STARTER_PROMPT_SENTINEL"),
    )


def _run_fs_health_for_diagnose(workspace_root: Path) -> FsHealth:
    """Build an :class:`FsHealth` snapshot for the diagnose CLI.

    Test seam: production delegates to :meth:`FsHealth.gather`; tests
    monkeypatch this attribute to inject a stubbed snapshot without
    running ``mdutil`` / ``.fseventsd`` probes.
    """
    return FsHealth.gather(workspace_root)


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
    display = resolve_active_display(None, ctx)
    display.emit_status("Ralph Workflow Diagnostics")

    workspace_scope = resolve_workspace_scope()

    _check_version(display=display, allow_network=ctx.console.is_terminal)
    config_ok = _check_git_repo(display=display)
    config_ok &= _check_configuration(config_path, cli_overrides, display=display)
    agent_missing = _check_agents_impl(cli_overrides, display=display)
    config_ok &= not agent_missing
    config_ok &= _check_mcp_servers(workspace_scope, display=display)
    config_ok &= _check_workspace_files(display=display)
    _check_capability_state(display=display)
    _check_filesystem_health(workspace_scope.root, display=display)

    # Pre-flight validation using policy system
    validation_ok = _run_preflight_validation(
        config_path, cli_overrides, workspace_scope, display=display
    )

    # Build and print next steps
    prompt_path = resolve_effective_prompt_path(workspace_scope.root, ctx.env)
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
    _print_next_steps_panel(next_steps, display=display)

    display.emit_blank_line()

    if not validation_ok:
        return 2
    if not config_ok:
        return 1
    return 0


def _check_capability_state(*, display: object) -> bool:
    """Check and display baseline capability state table.

    ``display`` is a :class:`ParallelDisplay`. The parameter is typed as
    ``object`` to keep this module free of a parallel_display import cycle
    in type-check; the runtime call site uses ``display.emit_status``.
    """
    from ralph.display.parallel_display import ParallelDisplay

    assert isinstance(display, ParallelDisplay)
    manager = SkillManager()
    manager.check_baseline_health()
    manager.check_skills_for_updates()
    state = load_capability_state()
    rows: list[tuple[object, ...]] = [
        (
            cap.name.replace("_", " ").title(),
            "Built-in",
            Text("OK \u2014 always available", style="theme.status.success"),
            Text("no"),
            "N/A",
        )
        for cap in STATIC_BUILTIN_CAPABILITIES
    ]
    managed_rows = [
        ("Web search (DuckDuckGo)", state.web_search),
        ("Page retrieval (visit_url)", state.visit_url),
        ("Docs MCP (localhost:6280)", state.docs_mcp),
        ("Skill bundles", state.skills),
    ]
    for label, entry in managed_rows:
        status_text = (
            Text(entry.status.value, style="theme.status.success")
            if entry.status == CapabilityStatus.INSTALLED_HEALTHY
            else Text(entry.status.value, style="theme.status.warning")
        )
        update_text = (
            Text("yes", style="theme.status.warning") if entry.update_available else Text("no")
        )
        last_ok = entry.last_check_ok_iso or "(never)"
        rows.append((label, "Managed", status_text, update_text, last_ok))
    _emit_simple_table(display, "Baseline Capabilities", rows)
    return True


def _emit_simple_table(display: object, title: str, rows: list[tuple[object, ...]]) -> None:
    """Emit a Table to a ParallelDisplay by routing through its console.

    Used by the diagnose helpers that need richer per-row formatting than
    the new emit_diagnose_* helpers can offer. Goes through the
    display's own ``_console`` so the section rule contract still fires.
    """
    from rich.table import Table

    from ralph.display.parallel_display import ParallelDisplay

    assert isinstance(display, ParallelDisplay)
    table = Table(title=title)
    for cell in rows[0] if rows else ("Check", "Status"):
        if isinstance(cell, Text):
            pass
    # Build a generic 5-column layout matching the row shape used by the
    # capability / git / configuration / workspace tables in this module.
    column_styles = [
        "theme.cat.meta",
        None,
        "theme.status.success",
        "theme.text.muted",
        "theme.text.muted",
    ]
    headers = ["Capability", "Type", "Status", "Update Available", "Last Checked"]
    if not rows:
        for header, style in zip(headers, column_styles, strict=False):
            table.add_column(header, style=style) if style else table.add_column(header)
    else:
        for header, style in zip(headers, column_styles, strict=False):
            table.add_column(header, style=style) if style else table.add_column(header)
    for row in rows:
        cells = list(row) + [None] * (5 - len(row))
        table.add_row(*(str(cell) if cell is not None else "-" for cell in cells[:5]))
    display.emit_renderable(table)


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
            "Claude Code (https://docs.claude.com/claude-code), "
            "Codex CLI (https://codex.openai.com), "
            "OpenCode (https://opencode.ai), "
            "or Google Anti Gravity (https://github.com/google-antigravity/antigravity-cli)."
        )

    if not validation_ok:
        steps.append(
            "Pre-flight validation failed: see the Pre-flight Validation table above. "
            "Fix policy errors with `ralph --regenerate-config` if config files were edited."
        )

    if not steps:
        steps.append(f"Run `{RUN_COMMAND}` to start the pipeline.")

    return steps


def _print_next_steps_panel(steps: list[str], *, display: object) -> None:
    """Print the Next steps panel through the consolidated display."""
    from ralph.display.parallel_display import ParallelDisplay

    assert isinstance(display, ParallelDisplay)
    content_parts: list[str] = []
    for i, step in enumerate(steps):
        if i > 0:
            content_parts.append("")
        content_parts.append(f"  \u2022 {step}")
    content_parts.append("")
    content_parts.append(
        f"New to Ralph Workflow? {GETTING_STARTED_DOC} \u2014 step-by-step walkthrough."
    )
    display.emit_info_panel(title="Next steps", content="\n".join(content_parts))


def _run_preflight_validation(
    config_path: Path | None,
    cli_overrides: dict[str, object] | None,
    workspace_scope: WorkspaceScope,
    *,
    display: object,
) -> bool:
    """Run pre-flight validation on policy configuration.

    Args:
        config_path: Optional path to config file.
        cli_overrides: CLI flag overrides.
        workspace_scope: Workspace scope.
        display: Active ParallelDisplay.

    Returns:
        True if validation passes, False otherwise.
    """
    from ralph.display.parallel_display import ParallelDisplay

    assert isinstance(display, ParallelDisplay)
    rows: list[tuple[object, ...]] = []

    try:
        config = load_config(config_path, cli_overrides, workspace_scope=workspace_scope)
        registry = AgentRegistry.from_config(config)

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
            rows.append(
                (
                    "Pre-flight",
                    Text(
                        "Skipped: project is not initialized yet (run `ralph --init`)",
                        style="theme.status.warning",
                    ),
                    "",
                    "",
                    "",
                )
            )
            _emit_simple_table(display, "Pre-flight Validation", rows)
            return True

        bundle = (
            load_policy(policy_dir, config=config)
            if config_path is not None
            else load_policy_for_workspace_scope(workspace_scope, config=config)
        )

        validate_agent_chains_satisfiable(bundle, registry)
        validate_recovery_config(bundle)

        rows.append(("Agent chains", Text("Satisfiable", style="theme.status.success"), "", "", ""))
        rows.append(("Recovery config", Text("Valid", style="theme.status.success"), "", "", ""))
        _emit_simple_table(display, "Pre-flight Validation", rows)
        return True

    except PolicyValidationError as e:
        rows.append(
            (
                "Policy validation",
                _status_text("Failed", e.message, "theme.status.error"),
                "",
                "",
                "",
            )
        )
        _emit_simple_table(display, "Pre-flight Validation", rows)
        return False
    except Exception as e:
        rows.append(("Pre-flight", _status_text("Error", str(e), "theme.status.error"), "", "", ""))
        _emit_simple_table(display, "Pre-flight Validation", rows)
        return False


def _check_version(*, display: object, allow_network: bool) -> None:
    """Report the installed version, latest known release, and how to upgrade."""
    from ralph.update_check import update_status

    rows: list[tuple[object, ...]] = []
    try:
        status = update_status(allow_network=allow_network)
    except Exception:
        return

    rows.append(("Installed version", status.current_version, "", "", ""))
    if status.disabled:
        rows.append(("Update check", Text("disabled", style="theme.text.muted"), "", "", ""))
        _emit_simple_table(display, "Version", rows)
        return

    if status.latest_version is None:
        rows.append(
            ("Latest release", Text("unknown (offline?)", style="theme.text.muted"), "", "", "")
        )
    elif status.update_available:
        rows.append(
            (
                "Latest release",
                _status_text("Update available", status.latest_version, "theme.status.warning"),
                "",
                "",
                "",
            )
        )
        rows.append(
            ("Detected install", status.install.kind.value, "", "", ""),
        )
        rows.append(("Upgrade with", status.install.upgrade_command, "", "", ""))
    else:
        rows.append(
            ("Latest release", Text("up to date", style="theme.status.success"), "", "", "")
        )
    _emit_simple_table(display, "Version", rows)


def _check_git_repo(*, display: object) -> bool:
    """Check git repository status."""
    from ralph.display.parallel_display import ParallelDisplay

    assert isinstance(display, ParallelDisplay)
    rows: list[tuple[object, ...]] = []

    try:
        repo_root = find_repo_root()
        rows.append(("Repository root", str(repo_root), "", "", ""))
    except Exception as e:
        rows.append(("Repository", _status_text("Error", str(e), "theme.status.error"), "", "", ""))
        _emit_simple_table(display, "Git Repository", rows)
        return False

    try:
        clean = is_repo_clean(repo_root)
        if clean:
            rows.append(("Working tree", Text("Clean", style="theme.status.success"), "", "", ""))
        else:
            rows.append(
                (
                    "Working tree",
                    Text("Has uncommitted changes", style="theme.status.warning"),
                    "",
                    "",
                    "",
                )
            )
    except Exception as e:
        rows.append(
            ("Working tree", _status_text("Error", str(e), "theme.status.error"), "", "", "")
        )

    _emit_simple_table(display, "Git Repository", rows)
    return True


def _check_configuration(
    config_path: Path | None,
    cli_overrides: dict[str, object] | None,
    *,
    display: object,
) -> bool:
    """Check configuration validity."""
    from ralph.display.parallel_display import ParallelDisplay

    assert isinstance(display, ParallelDisplay)
    rows: list[tuple[object, ...]] = []

    try:
        workspace_scope = None if config_path is not None else resolve_workspace_scope()
        config = load_config(config_path, cli_overrides, workspace_scope=workspace_scope)
        rows.append(("Config loaded", Text("Success", style="theme.status.success"), "", "", ""))
        rows.append(("Developer iters", str(config.general.developer_iters), "", "", ""))
        rows.append(
            ("Checkpoint enabled", str(config.general.workflow.checkpoint_enabled), "", "", "")
        )
    except Exception as e:
        rows.append(
            ("Config loaded", _status_text("Error", str(e), "theme.status.error"), "", "", "")
        )
        _emit_simple_table(display, "Configuration", rows)
        return False

    _emit_simple_table(display, "Configuration", rows)
    return True


def check_agents(
    cli_overrides: dict[str, object] | None,
    *,
    display_context: DisplayContext | None = None,
) -> bool:
    """Check agent availability and return True if any agent is missing from PATH.

    Public wrapper: resolves the active display from ``display_context``
    and delegates to :func:`_check_agents_impl`.
    """
    ctx = display_context if display_context is not None else make_display_context()
    display = resolve_active_display(None, ctx)
    return _check_agents_impl(cli_overrides, display=display)


def _check_agents_impl(
    cli_overrides: dict[str, object] | None,
    *,
    display: object,
) -> bool:
    """Internal: check agent availability with a pre-resolved display.

    Returns True if any agent is missing from PATH.
    """
    from ralph.display.parallel_display import ParallelDisplay

    assert isinstance(display, ParallelDisplay)
    rows: list[tuple[object, ...]] = []

    any_missing = False
    try:
        config = load_config(None, cli_overrides, workspace_scope=resolve_workspace_scope())
        registry = AgentRegistry.from_config(config)
        agent_names = registry.list_agents()
        if not agent_names:
            rows.append(
                ("(none)", Text("No agents configured", style="theme.status.warning"), "-", "", "")
            )
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
                rows.append((name, config_cell, path_status, "", ""))
    except Exception as e:
        rows.append(("Agents", _status_text("Error", str(e), "theme.status.error"), "-", "", ""))
        _emit_simple_table(display, "Agents", rows)
        return True

    _emit_simple_table(display, "Agents", rows)
    return any_missing


def _check_mcp_servers(
    workspace_scope: WorkspaceScope,
    *,
    display: object,
) -> bool:
    """Render custom MCP server health and per-agent transport compatibility."""
    from ralph.display.parallel_display import ParallelDisplay

    assert isinstance(display, ParallelDisplay)

    _print_effective_session_mcp_inventory(display, workspace_scope.root)

    ok, healthy_servers = _render_custom_mcp_server_table(display, workspace_scope.root)
    if not ok or not healthy_servers:
        return ok

    _print_agent_transport_compatibility(display, healthy_servers, workspace_scope.root)
    return True


def _render_custom_mcp_server_table(
    display: object, workspace_root: Path
) -> tuple[bool, tuple[UpstreamMcpServer, ...]]:
    """Print custom MCP health table and return whether it succeeded."""
    from ralph.display.parallel_display import ParallelDisplay

    assert isinstance(display, ParallelDisplay)
    upstreams = mcp_toml_as_upstreams(workspace_root)
    rows: list[tuple[object, ...]] = []
    if not upstreams:
        rows.append(
            (
                "(none)",
                "-",
                Text("No custom MCP servers configured", style="theme.status.warning"),
                "-",
                "-",
            )
        )
        display.emit_diagnose_servers_table(rows)
        return True, ()

    try:
        report = validate_upstream_mcp_servers(upstreams, strict=False)
    except Exception as exc:
        rows.append(
            (
                "(validator)",
                "-",
                _status_text("Error", str(exc), "theme.status.error"),
                "-",
                "-",
            )
        )
        display.emit_diagnose_servers_table(rows)
        return False, ()

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
        rows.append(
            (
                entry.name,
                entry.transport,
                status,
                str(entry.tool_count),
                detail or "-",
            )
        )

    display.emit_diagnose_servers_table(rows)

    healthy_names = {r.name for r in report.servers if r.ok}
    healthy_servers = tuple(s for s in upstreams if s.name in healthy_names)
    return True, healthy_servers


def _print_agent_transport_compatibility(
    display: object,
    healthy_servers: tuple[UpstreamMcpServer, ...],
    workspace_root: Path,
) -> None:
    """Print per-agent MCP transport compatibility for healthy custom servers."""
    from ralph.display.parallel_display import ParallelDisplay

    assert isinstance(display, ParallelDisplay)
    rows: list[tuple[object, ...]] = []

    probes = probe_agent_transports(healthy_servers, workspace_path=workspace_root)
    by_server: dict[str, dict[str, Text]] = {}
    for probe in probes:
        if probe.note and probe.ok:
            cell = Text("-", style="theme.status.warning")
        elif probe.ok:
            cell = Text("\u2713", style="theme.status.success")
        else:
            cell = Text("\u2717", style="theme.status.error")
        by_server.setdefault(probe.server_name, {})[probe.transport.value] = cell

    for server in healthy_servers:
        cells = by_server.get(server.name, {})
        rows.append(
            (
                server.name,
                cells.get("claude", Text("-")),
                cells.get("codex", Text("-")),
                cells.get("opencode", Text("-")),
                cells.get("agy", Text("-")),
            )
        )

    display.emit_diagnose_probe_table(rows)


def _print_effective_session_mcp_inventory(display: object, workspace_root: Path) -> None:
    """Print the effective session MCP inventory table through the active display."""
    from ralph.display.parallel_display import ParallelDisplay

    assert isinstance(display, ParallelDisplay)
    effective_mcp = resolve_effective_session_mcp_plan(
        workspace_root,
        agent_upstream_servers=(
            *load_existing_claude_upstream_servers(workspace_root),
            *load_existing_agy_upstream_servers(workspace_root),
        ),
    )
    rows: list[tuple[object, ...]] = []
    if effective_mcp.effective_servers:
        rows.extend(
            (
                server.name,
                server.origin,
                server.transport,
                _inventory_exposure(server.origin),
            )
            for server in effective_mcp.effective_servers
        )
    else:
        rows.append(("(none)", "-", "-", "No effective session MCP servers"))
    display.emit_diagnose_inventory_table(rows)


def _inventory_exposure(origin: str) -> str:
    if origin == "custom":
        return "proxied via ralph_custom__*"
    return "proxied via ralph_upstream__*"


def _check_workspace_files(*, display: object) -> bool:
    """Check workspace files."""
    from ralph.display.parallel_display import ParallelDisplay

    assert isinstance(display, ParallelDisplay)
    rows: list[tuple[object, ...]] = []

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
            rows.append(
                (
                    file_label,
                    _status_text("Exists", f"{size} bytes", "theme.status.success"),
                    "",
                    "",
                    "",
                )
            )
        else:
            rows.append((file_label, Text("Not found", style="theme.status.warning"), "", "", ""))

    _emit_simple_table(display, "Workspace Files", rows)
    return True


def _check_filesystem_health(workspace_root: Path, *, display: object) -> bool:
    """Render the FsHealth snapshot (RFC-013 P4) into the diagnose output.

    Builds the section from a real :class:`FsHealth.gather` (via the
    :func:`_run_fs_health_for_diagnose` test seam) so the operator-facing
    "External-volume filesystem hygiene" mitigations surface here as the
    docs/sphinx/diagnostics.md documentation promises. The function
    always returns True — fs-health findings are advisory and must not
    flip ``config_ok``.
    """
    from ralph.display.parallel_display import ParallelDisplay

    assert isinstance(display, ParallelDisplay)
    fs_health = _run_fs_health_for_diagnose(workspace_root)

    spotlight_cell: Text
    if fs_health.spotlight_indexing_enabled is True:
        spotlight_cell = Text("Enabled", style="theme.status.warning")
    elif fs_health.spotlight_indexing_enabled is False:
        spotlight_cell = Text("Disabled", style="theme.status.success")
    else:
        spotlight_cell = Text("Unknown", style="theme.text.muted")

    journal_bytes = fs_health.fsevents_journal_bytes
    if journal_bytes is None:
        journal_cell: Text = Text("Unknown", style="theme.text.muted")
    else:
        journal_mb = journal_bytes / (1024 * 1024)
        style = (
            "theme.status.warning"
            if journal_bytes > 50 * 1024 * 1024
            else "theme.status.success"
        )
        journal_cell = Text(f"{journal_mb:.1f} MB", style=style)

    warnings_count = len(fs_health.warnings)
    if warnings_count == 0:
        warnings_cell: Text = Text("none", style="theme.status.success")
    else:
        warnings_cell = Text(
            f"{warnings_count} warning(s)", style="theme.status.warning"
        )

    rows: list[tuple[object, ...]] = [
        (
            fs_health.volume_root,
            spotlight_cell,
            journal_cell,
            warnings_cell,
            "",
        )
    ]
    _emit_simple_table(display, "Filesystem Health", rows)

    if fs_health.warnings:
        warning_lines = "\n".join(f"  \u2022 {warning}" for warning in fs_health.warnings)
        warning_lines = warning_lines + "\n\n  See: External-volume filesystem hygiene in docs."
        display.emit_info_panel(title="Filesystem Health Warnings", content=warning_lines)

    return True


def _status_text(label: str, detail: str, style: str) -> Text:
    text = Text()
    text.append(f"{label}:", style=style)
    text.append(" ")
    text.append(detail)
    return text


def check_git_repo(
    *,
    display_context: DisplayContext | None = None,
) -> bool:
    """Public check helper that resolves an active display from a context."""
    ctx = display_context if display_context is not None else make_display_context()
    display = resolve_active_display(None, ctx)
    return _check_git_repo(display=display)


def check_configuration(
    config_path: Path | None,
    cli_overrides: dict[str, object] | None,
    *,
    display_context: DisplayContext | None = None,
) -> bool:
    """Public check helper that resolves an active display from a context."""
    ctx = display_context if display_context is not None else make_display_context()
    display = resolve_active_display(None, ctx)
    return _check_configuration(config_path, cli_overrides, display=display)


def check_mcp_servers(
    workspace_scope: WorkspaceScope,
    *,
    display_context: DisplayContext | None = None,
) -> bool:
    """Public check helper that resolves an active display from a context."""
    ctx = display_context if display_context is not None else make_display_context()
    display = resolve_active_display(None, ctx)
    return _check_mcp_servers(workspace_scope, display=display)


def check_workspace_files(
    *,
    display_context: DisplayContext | None = None,
) -> bool:
    """Public check helper that resolves an active display from a context."""
    ctx = display_context if display_context is not None else make_display_context()
    display = resolve_active_display(None, ctx)
    return _check_workspace_files(display=display)
