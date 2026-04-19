"""Probe per-agent MCP wiring against validated upstream servers.

After :mod:`ralph.mcp.upstream_validation` has confirmed that each upstream
MCP server is reachable from Ralph, this module synthesizes the
agent-specific config payload Ralph would emit for Claude/Codex/OpenCode and
re-runs the same MCP handshake to confirm the wire is shaped correctly.

The probe is *self-contained*: it never spawns the agent binaries themselves.
The MCP JSON-RPC handshake is identical across the supported agents so
Ralph's own client is a faithful reference implementation.
"""

from __future__ import annotations

import json
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, cast

from loguru import logger

from ralph.agents.transport_emit import (
    _build_opencode_provider_config,
    _claude_mcp_config,
    _prepare_codex_home_with_upstreams,
)
from ralph.config.enums import AgentTransport
from ralph.mcp.startup import (
    PreflightError,
    parse_http_endpoint,
    post_http_jsonrpc_with_session,
)
from ralph.mcp.tool_names import RALPH_MCP_SERVER_NAME
from ralph.mcp.upstream_client import make_upstream_client
from ralph.mcp.upstream_models import UpstreamCallError

if TYPE_CHECKING:
    from collections.abc import Iterable
    from datetime import timedelta

    from ralph.mcp.upstream_config import UpstreamMcpServer

_DEFAULT_TRANSPORTS: tuple[AgentTransport, ...] = (
    AgentTransport.CLAUDE,
    AgentTransport.CODEX,
    AgentTransport.OPENCODE,
)


class AgentTransportProbeError(RuntimeError):
    """Raised when the synthesized agent config payload is malformed."""


@dataclass(frozen=True)
class AgentProbeReport:
    transport: AgentTransport
    server_name: str
    ok: bool
    error: str | None = None
    note: str | None = None


def probe_agent_transports(
    servers: Iterable[UpstreamMcpServer],
    *,
    transports: Iterable[AgentTransport] = _DEFAULT_TRANSPORTS,
    workspace_path: Path | None = None,
    timeout: timedelta | None = None,
) -> tuple[AgentProbeReport, ...]:
    """Confirm Ralph's per-agent MCP wiring reaches each server.

    Args:
        servers: Iterable of validated upstream servers.
        transports: Agent transports to probe. Defaults to all supported.
        workspace_path: Optional workspace path used by Codex prep helpers.
        timeout: Reserved; subprocess and HTTP probes use the per-call
            timeout configured via ``RALPH_MCP_PREFLIGHT_TIMEOUT_MS``.

    Returns:
        One report per (transport, server) pair.
    """

    del timeout  # individual probes manage their own per-call budgets
    server_list = list(servers)
    transport_list = list(transports)
    reports: list[AgentProbeReport] = [
        _probe_pair(transport, server, workspace_path)
        for transport in transport_list
        for server in server_list
    ]
    return tuple(reports)


def _probe_pair(
    transport: AgentTransport,
    server: UpstreamMcpServer,
    workspace_path: Path | None,
) -> AgentProbeReport:
    try:
        if transport == AgentTransport.CLAUDE:
            return _probe_claude(server, workspace_path)
        if transport == AgentTransport.CODEX:
            return _probe_codex(server, workspace_path)
        if transport == AgentTransport.OPENCODE:
            return _probe_opencode(server, workspace_path)
    except (PreflightError, UpstreamCallError, ValueError, OSError) as exc:
        return AgentProbeReport(
            transport=transport,
            server_name=server.name,
            ok=False,
            error=_redact(server, exc),
        )
    except AgentTransportProbeError as exc:
        return AgentProbeReport(
            transport=transport,
            server_name=server.name,
            ok=False,
            error=_redact(server, exc),
        )
    return AgentProbeReport(
        transport=transport,
        server_name=server.name,
        ok=False,
        error=f"unsupported transport '{transport}'",
    )


def _probe_claude(
    server: UpstreamMcpServer, workspace_path: Path | None
) -> AgentProbeReport:
    if server.transport == "stdio":
        return AgentProbeReport(
            transport=AgentTransport.CLAUDE,
            server_name=server.name,
            ok=True,
            note="skipped (stdio proxied by Claude CLI)",
        )
    if not server.url:
        raise AgentTransportProbeError(
            f"server '{server.name}' is missing url for Claude http transport"
        )
    config_blob = _claude_mcp_config(server.url, workspace_path=workspace_path)
    parsed = _parse_json_obj(config_blob, "Claude MCP config")
    mcp_servers = parsed.get("mcpServers")
    if not isinstance(mcp_servers, dict):
        raise AgentTransportProbeError("Claude MCP config missing 'mcpServers'")
    raw_entry = cast("dict[str, object]", mcp_servers).get(RALPH_MCP_SERVER_NAME)
    if not isinstance(raw_entry, dict):
        raise AgentTransportProbeError(
            "Claude MCP config missing Ralph entry; injected wiring is broken"
        )
    entry = cast("dict[str, object]", raw_entry)
    if entry.get("url") != server.url:
        raise AgentTransportProbeError(
            f"Claude MCP config Ralph url='{entry.get('url')!r}' does not match server.url"
        )
    _http_handshake(server.url)
    return AgentProbeReport(
        transport=AgentTransport.CLAUDE, server_name=server.name, ok=True
    )


def _probe_codex(
    server: UpstreamMcpServer, workspace_path: Path | None
) -> AgentProbeReport:
    codex_home_str, _upstreams = _prepare_codex_home_with_upstreams(
        endpoint=None,
        workspace_path=workspace_path,
        existing_home=None,
        system_prompt_file=None,
    )
    codex_home = Path(codex_home_str)
    config_path = codex_home / "config.toml"
    config_text = config_path.read_text(encoding="utf-8") if config_path.exists() else ""
    # Append the synthetic server so the probe verifies how Ralph would write it.
    config_text = _augment_codex_config_with_server(config_text, server)
    parsed = cast("dict[str, object]", tomllib.loads(config_text))
    mcp_servers = parsed.get("mcp_servers")
    if not isinstance(mcp_servers, dict):
        raise AgentTransportProbeError(
            "Codex config.toml missing [mcp_servers] table after probe synthesis"
        )
    raw_entry = cast("dict[str, object]", mcp_servers).get(server.name)
    if not isinstance(raw_entry, dict):
        raise AgentTransportProbeError(
            f"Codex config.toml missing [mcp_servers.{server.name}] table"
        )
    entry = cast("dict[str, object]", raw_entry)
    if server.transport == "http" and entry.get("url") != server.url:
        raise AgentTransportProbeError(
            f"Codex config.toml mcp_servers.{server.name}.url mismatch"
        )
    if server.transport == "stdio" and entry.get("command") != server.command:
        raise AgentTransportProbeError(
            f"Codex config.toml mcp_servers.{server.name}.command mismatch"
        )
    _server_handshake(server)
    return AgentProbeReport(
        transport=AgentTransport.CODEX, server_name=server.name, ok=True
    )


def _augment_codex_config_with_server(
    base_config: str, server: UpstreamMcpServer
) -> str:
    section_lines = [f"[mcp_servers.{server.name}]"]
    if server.transport == "http":
        section_lines.append(f'url = "{server.url}"')
    else:
        section_lines.append(f'command = "{server.command}"')
        if server.args:
            args_repr = ", ".join(json.dumps(arg) for arg in server.args)
            section_lines.append(f"args = [{args_repr}]")
    section_lines.append("enabled = true")
    section = "\n".join(section_lines) + "\n"
    if base_config.strip():
        return base_config.rstrip() + "\n\n" + section
    return section


def _probe_opencode(
    server: UpstreamMcpServer, workspace_path: Path | None
) -> AgentProbeReport:
    del workspace_path
    if server.transport == "stdio":
        return AgentProbeReport(
            transport=AgentTransport.OPENCODE,
            server_name=server.name,
            ok=True,
            note="skipped (stdio proxied by OpenCode CLI)",
        )
    if not server.url:
        raise AgentTransportProbeError(
            f"server '{server.name}' is missing url for OpenCode http transport"
        )
    inner: dict[str, object] = {"type": "remote", "url": server.url}
    existing_payload_obj: dict[str, object] = {"mcp": {server.name: inner}}
    existing_payload = json.dumps(existing_payload_obj)
    config_text, _upstreams = _build_opencode_provider_config(
        existing_payload, server.url
    )
    parsed = _parse_json_obj(config_text, "OpenCode provider config")
    mcp_section = parsed.get("mcp")
    if not isinstance(mcp_section, dict):
        raise AgentTransportProbeError("OpenCode config missing 'mcp' section")
    typed_mcp = cast("dict[str, object]", mcp_section)
    raw_ralph_entry = typed_mcp.get(RALPH_MCP_SERVER_NAME)
    if not isinstance(raw_ralph_entry, dict):
        raise AgentTransportProbeError(
            "OpenCode config missing Ralph mcp entry after synthesis"
        )
    ralph_entry = cast("dict[str, object]", raw_ralph_entry)
    if ralph_entry.get("type") != "remote" or ralph_entry.get("url") != server.url:
        raise AgentTransportProbeError(
            "OpenCode Ralph mcp entry shape mismatch (type/url)"
        )
    _http_handshake(server.url)
    return AgentProbeReport(
        transport=AgentTransport.OPENCODE, server_name=server.name, ok=True
    )


def _http_handshake(endpoint: str) -> None:
    target = parse_http_endpoint(endpoint)
    initialize_payload: dict[str, object] = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "ralph-agent-probe", "version": "0"},
        },
    }
    initialize_response, session_id = post_http_jsonrpc_with_session(
        endpoint, target, initialize_payload
    )
    err = initialize_response.get("error")
    if err is not None:
        raise AgentTransportProbeError(f"initialize failed: {err}")
    initialized_payload: dict[str, object] = {
        "jsonrpc": "2.0",
        "method": "notifications/initialized",
        "params": {},
    }
    _, session_id = post_http_jsonrpc_with_session(
        endpoint, target, initialized_payload, session_id=session_id
    )
    tools_payload: dict[str, object] = {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/list",
        "params": {},
    }
    tools_response, _ = post_http_jsonrpc_with_session(
        endpoint, target, tools_payload, session_id=session_id
    )
    err = tools_response.get("error")
    if err is not None:
        raise AgentTransportProbeError(f"tools/list failed: {err}")


def _server_handshake(server: UpstreamMcpServer) -> None:
    if server.transport == "http":
        if not server.url:
            raise AgentTransportProbeError(
                f"server '{server.name}' is missing url for http handshake"
            )
        _http_handshake(server.url)
        return
    client = make_upstream_client(server)
    client.list_tools()


def _parse_json_obj(text: str, label: str) -> dict[str, object]:
    try:
        decoded: object = json.loads(text)
    except json.JSONDecodeError as exc:
        raise AgentTransportProbeError(f"{label} is not valid JSON: {exc}") from exc
    if not isinstance(decoded, dict):
        raise AgentTransportProbeError(f"{label} top-level is not an object")
    return cast("dict[str, object]", decoded)


def _redact(server: UpstreamMcpServer, exc: BaseException) -> str:
    message = str(exc)
    for value in server.env.values():
        if value:
            message = message.replace(value, "***")
    return message


def _log_probe_report(report: AgentProbeReport) -> None:
    if report.ok:
        logger.debug(
            "Agent transport probe ok: server={} transport={}",
            report.server_name,
            report.transport,
        )
    else:
        logger.warning(
            "Agent transport probe failed: server={} transport={} error={}",
            report.server_name,
            report.transport,
            report.error,
        )


__all__ = [
    "AgentProbeReport",
    "AgentTransportProbeError",
    "probe_agent_transports",
]
