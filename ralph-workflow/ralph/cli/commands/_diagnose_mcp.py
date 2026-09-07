from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from rich.text import Text

from ralph.mcp.session_plan import resolve_effective_session_mcp_plan
from ralph.mcp.transport.agy import load_existing_agy_upstream_servers
from ralph.mcp.transport.claude import load_existing_claude_upstream_servers
from ralph.mcp.transport.common import mcp_toml_as_upstreams
from ralph.mcp.upstream.agent_probe import probe_agent_transports
from ralph.mcp.upstream.validation import validate_upstream_mcp_servers

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path

    from ralph.display.parallel_display import ParallelDisplay
    from ralph.mcp.effective_session_mcp_plan import EffectiveSessionMcpPlan
    from ralph.mcp.upstream.agent_probe import AgentProbeReport
    from ralph.mcp.upstream.config import UpstreamMcpServer
    from ralph.mcp.upstream.validation import UpstreamValidationReport
    from ralph.workspace.scope import WorkspaceScope


class _McpLoader(Protocol):
    def __call__(self, workspace_path: Path | None) -> tuple[UpstreamMcpServer, ...]: ...


class _UpstreamLoader(_McpLoader, Protocol): ...


class _Validator(Protocol):
    def __call__(
        self, servers: Iterable[UpstreamMcpServer], *, strict: bool
    ) -> UpstreamValidationReport: ...


class _Prober(Protocol):
    def __call__(
        self, servers: Iterable[UpstreamMcpServer], *, workspace_path: Path | None
    ) -> tuple[AgentProbeReport, ...]: ...


class _PlanResolver(Protocol):
    def __call__(
        self,
        workspace_path: Path | None,
        *,
        agent_upstream_servers: tuple[UpstreamMcpServer, ...] = (),
        provider_visible_server_names: tuple[str, ...] = (),
    ) -> EffectiveSessionMcpPlan: ...


def check_mcp_servers(
    workspace_scope: WorkspaceScope,
    *,
    display: ParallelDisplay,
    mcp_loader: _McpLoader | None = None,
    validator: _Validator | None = None,
    prober: _Prober | None = None,
    plan_resolver: _PlanResolver | None = None,
    claude_loader: _UpstreamLoader | None = None,
    agy_loader: _UpstreamLoader | None = None,
) -> bool:
    resolved_mcp_loader = mcp_loader or mcp_toml_as_upstreams
    resolved_validator = validator or validate_upstream_mcp_servers
    resolved_prober = prober or probe_agent_transports
    resolved_plan_resolver = plan_resolver or resolve_effective_session_mcp_plan
    resolved_claude_loader = claude_loader or load_existing_claude_upstream_servers
    resolved_agy_loader = agy_loader or load_existing_agy_upstream_servers
    _print_effective_session_mcp_inventory(
        display,
        workspace_scope.root,
        plan_resolver=resolved_plan_resolver,
        claude_loader=resolved_claude_loader,
        agy_loader=resolved_agy_loader,
    )
    ok, healthy_servers = _render_custom_mcp_server_table(
        display, workspace_scope.root, mcp_loader=resolved_mcp_loader, validator=resolved_validator
    )
    if not ok or not healthy_servers:
        return ok
    _print_agent_transport_compatibility(
        display, healthy_servers, workspace_scope.root, prober=resolved_prober
    )
    return True


def _render_custom_mcp_server_table(
    display: ParallelDisplay,
    workspace_root: Path,
    *,
    mcp_loader: _McpLoader,
    validator: _Validator,
) -> tuple[bool, tuple[UpstreamMcpServer, ...]]:
    upstreams = mcp_loader(workspace_root)
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
        report = validator(upstreams, strict=False)
    except Exception as exc:
        rows.append(
            ("(validator)", "-", _status_text("Error", str(exc), "theme.status.error"), "-", "-")
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
        rows.append((entry.name, entry.transport, status, str(entry.tool_count), detail or "-"))
    display.emit_diagnose_servers_table(rows)
    healthy_names = {entry.name for entry in report.servers if entry.ok}
    return True, tuple(server for server in upstreams if server.name in healthy_names)


def _print_agent_transport_compatibility(
    display: ParallelDisplay,
    healthy_servers: tuple[UpstreamMcpServer, ...],
    workspace_root: Path,
    *,
    prober: _Prober = probe_agent_transports,
) -> None:
    rows: list[tuple[object, ...]] = []
    probes = prober(healthy_servers, workspace_path=workspace_root)
    by_server: dict[str, dict[str, Text]] = {}
    for probe in probes:
        cell = (
            Text("-", style="theme.status.warning")
            if probe.note and probe.ok
            else Text("✓", style="theme.status.success")
            if probe.ok
            else Text("✗", style="theme.status.error")
        )
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


def _print_effective_session_mcp_inventory(
    display: ParallelDisplay,
    workspace_root: Path,
    *,
    plan_resolver: _PlanResolver,
    claude_loader: _UpstreamLoader,
    agy_loader: _UpstreamLoader,
) -> None:
    effective_mcp = plan_resolver(
        workspace_root,
        agent_upstream_servers=(*claude_loader(workspace_root), *agy_loader(workspace_root)),
    )
    rows: list[tuple[object, ...]] = [
        (server.name, server.origin, server.transport, _inventory_exposure(server.origin))
        for server in effective_mcp.effective_servers
    ]
    if not rows:
        rows.append(("(none)", "-", "-", "No effective session MCP servers"))
    display.emit_diagnose_inventory_table(rows)


def _inventory_exposure(origin: str) -> str:
    return "proxied via ralph_custom__*" if origin == "custom" else "proxied via ralph_upstream__*"


def _status_text(label: str, detail: str, style: str) -> Text:
    text = Text()
    text.append(f"{label}:", style=style)
    text.append(" ")
    text.append(detail)
    return text
