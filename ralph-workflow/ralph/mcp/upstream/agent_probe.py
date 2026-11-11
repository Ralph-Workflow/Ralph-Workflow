
from __future__ import annotations

import json
import tomllib
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, cast

import tomli_w
from loguru import logger

import ralph.mcp.transport.agy as _agy_transport
import ralph.mcp.transport.claude as _claude_transport
import ralph.mcp.transport.codex as _codex_transport
import ralph.mcp.transport.kimi as _kimi_transport
import ralph.mcp.transport.opencode as _opencode_transport
from ralph.config.enums import AgentTransport
from ralph.mcp.protocol.startup import (
    PreflightError,
    initialize_request,
    initialized_notification,
    legacy_sse_jsonrpc_exchange,
    looks_like_legacy_sse_endpoint,
    parse_http_endpoint,
    post_http_jsonrpc_with_session,
    tools_list_request,
)
from ralph.mcp.server._schema_flavor import normalize_schema_for_function_calling
from ralph.mcp.tools.names import RALPH_MCP_SERVER_NAME
from ralph.mcp.upstream._agent_transport_probe_error import AgentTransportProbeError
from ralph.mcp.upstream.client import make_upstream_client
from ralph.mcp.upstream.config import UpstreamMcpServer
from ralph.mcp.upstream.models import UpstreamCallError, UpstreamTool

if TYPE_CHECKING:
    from datetime import timedelta

_DEFAULT_TRANSPORTS: tuple[AgentTransport, ...] = (
    AgentTransport.CLAUDE,
    AgentTransport.CLAUDE_INTERACTIVE,
    AgentTransport.CODEX,
    AgentTransport.OPENCODE,
    AgentTransport.AGY,
    AgentTransport.KIMI,
)


@dataclass(frozen=True)
class AgentProbeReport:

    transport: AgentTransport
    server_name: str
    ok: bool
    error: str | None = None
    note: str | None = None


_ClaudeMcpConfigFn = Callable[[str], str]
_BuildOpencodeProviderConfigFn = Callable[
    [str | None, str], tuple[str, tuple[UpstreamMcpServer, ...]]
]


def probe_agent_transports(
    servers: Iterable[UpstreamMcpServer],
    *,
    transports: Iterable[AgentTransport] = _DEFAULT_TRANSPORTS,
    workspace_path: Path | None = None,
    timeout: timedelta | None = None,
) -> tuple[AgentProbeReport, ...]:

    del timeout
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
    report: AgentProbeReport
    try:
        if transport in (AgentTransport.CLAUDE, AgentTransport.CLAUDE_INTERACTIVE):
            report = _probe_claude(server, workspace_path, transport=transport)
        elif transport == AgentTransport.CODEX:
            report = _probe_codex(server, workspace_path)
        elif transport == AgentTransport.OPENCODE:
            report = _probe_opencode(server, workspace_path)
        elif transport == AgentTransport.AGY:
            report = _probe_agy(server, workspace_path)
        elif transport == AgentTransport.KIMI:
            report = _probe_kimi(server, workspace_path)
        else:
            report = AgentProbeReport(
                transport=transport,
                server_name=server.name,
                ok=False,
                error=f"unsupported transport '{transport}'",
            )
    except (PreflightError, UpstreamCallError, ValueError, OSError) as exc:
        report = AgentProbeReport(
            transport=transport,
            server_name=server.name,
            ok=False,
            error=_redact(server, exc),
        )
    except AgentTransportProbeError as exc:
        report = AgentProbeReport(
            transport=transport,
            server_name=server.name,
            ok=False,
            error=_redact(server, exc),
        )
    return report


def _probe_claude(
    server: UpstreamMcpServer,
    workspace_path: Path | None,
    *,
    transport: AgentTransport,
) -> AgentProbeReport:
    if server.transport == "stdio":
        return AgentProbeReport(
            transport=transport,
            server_name=server.name,
            ok=True,
            note="skipped (stdio proxied by Claude CLI)",
        )
    if not server.url:
        raise AgentTransportProbeError(
            f"server '{server.name}' is missing url for Claude http transport"
        )
    config_blob = _claude_transport.claude_mcp_config(server.url)
    _validate_mcp_json_and_handshake(
        server,
        config_blob,
        "Claude MCP config",
        ralph_url_key="url",
    )
    return AgentProbeReport(transport=transport, server_name=server.name, ok=True)


def _probe_codex(server: UpstreamMcpServer, workspace_path: Path | None) -> AgentProbeReport:
    codex_home_str, _upstreams = _codex_transport.prepare_codex_home_with_upstreams(
        endpoint=None,
        workspace_path=workspace_path,
        existing_home=None,
        master_prompt_file=None,
    )
    try:
        codex_home = Path(codex_home_str)
        config_path = codex_home / "config.toml"
        config_text = config_path.read_text(encoding="utf-8") if config_path.exists() else ""
        parsed = (
            cast("dict[str, object]", tomllib.loads(config_text)) if config_text.strip() else {}
        )
        mcp_servers = parsed.get("mcp_servers") if isinstance(parsed, dict) else None
        existing_servers = (
            cast("dict[str, object]", mcp_servers) if isinstance(mcp_servers, dict) else None
        )
        if existing_servers is None or server.name not in existing_servers:
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
        server_handshake(server)
        return AgentProbeReport(transport=AgentTransport.CODEX, server_name=server.name, ok=True)
    finally:
        _codex_transport.release_codex_home(codex_home_str)


def _augment_codex_config_with_server(base_config: str, server: UpstreamMcpServer) -> str:
    config: dict[str, object] = {}
    if base_config.strip():
        parsed: object = tomllib.loads(base_config)
        if isinstance(parsed, dict):
            config = cast("dict[str, object]", parsed)
    existing = config.get("mcp_servers")
    servers: dict[str, object] = (
        dict(cast("dict[str, object]", existing)) if isinstance(existing, dict) else {}
    )
    entry: dict[str, object] = {}
    if server.transport == "http":
        entry["url"] = server.url or ""
    else:
        entry["command"] = server.command or ""
        if server.args:
            entry["args"] = list(server.args)
    entry["enabled"] = True
    servers[server.name] = entry
    merged: dict[str, object] = {**config, "mcp_servers": servers}
    return tomli_w.dumps(merged)


def _probe_opencode(server: UpstreamMcpServer, workspace_path: Path | None) -> AgentProbeReport:
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
    config_text, _upstreams = _opencode_transport.build_opencode_provider_config(
        existing_payload, server.url
    )
    parsed = _parse_json_obj(config_text, "OpenCode provider config")
    mcp_section = parsed.get("mcp")
    if not isinstance(mcp_section, dict):
        raise AgentTransportProbeError("OpenCode config missing 'mcp' section")
    typed_mcp = cast("dict[str, object]", mcp_section)
    raw_ralph_entry = typed_mcp.get(RALPH_MCP_SERVER_NAME)
    if not isinstance(raw_ralph_entry, dict):
        raise AgentTransportProbeError("OpenCode config missing Ralph mcp entry after synthesis")
    ralph_entry = cast("dict[str, object]", raw_ralph_entry)
    if ralph_entry.get("type") != "remote" or ralph_entry.get("url") != server.url:
        raise AgentTransportProbeError("OpenCode Ralph mcp entry shape mismatch (type/url)")
    http_handshake(server.url)
    return AgentProbeReport(transport=AgentTransport.OPENCODE, server_name=server.name, ok=True)


def _probe_agy(server: UpstreamMcpServer, workspace_path: Path | None) -> AgentProbeReport:
    del workspace_path
    if server.transport == "stdio":
        return AgentProbeReport(
            transport=AgentTransport.AGY,
            server_name=server.name,
            ok=True,
            note="skipped (stdio proxied by AGY CLI)",
        )
    if not server.url:
        raise AgentTransportProbeError(
            f"server '{server.name}' is missing url for AGY http transport"
        )
    config_blob = _agy_transport.agy_mcp_config(server.url)
    _validate_mcp_json_and_handshake(
        server,
        config_blob,
        "AGY MCP config",
        ralph_url_key="serverUrl",
    )
    return AgentProbeReport(transport=AgentTransport.AGY, server_name=server.name, ok=True)


def _probe_kimi(server: UpstreamMcpServer, workspace_path: Path | None) -> AgentProbeReport:
    del workspace_path
    if server.transport == "stdio":
        return AgentProbeReport(
            transport=AgentTransport.KIMI,
            server_name=server.name,
            ok=True,
            note="skipped (stdio proxied by Kimi CLI)",
        )
    if not server.url:
        raise AgentTransportProbeError(
            f"server '{server.name}' is missing url for Kimi http transport"
        )
    config_blob = _kimi_transport.kimi_mcp_config(server.url)
    _validate_mcp_json_and_handshake(
        server,
        config_blob,
        "Kimi MCP config",
        ralph_url_key="url",
    )
    if server.url is not None:
        for tool in _list_upstream_tools_for_probe(server):
            _assert_kimi_relayable_schema(server.name, tool)
    return AgentProbeReport(transport=AgentTransport.KIMI, server_name=server.name, ok=True)


def _list_upstream_tools_for_probe(server: UpstreamMcpServer) -> tuple[UpstreamTool, ...]:
    client = make_upstream_client(server)
    return tuple(client.list_tools())


_KIMI_COMPOSITION_KEYWORDS: frozenset[str] = frozenset({"oneOf", "anyOf", "allOf", "not"})


def _kimi_schema_violations(schema: object, path: str) -> list[str]:
    if not isinstance(schema, dict):
        return []
    violations: list[str] = []
    has_type = "type" in schema
    if has_type and any(k in schema for k in _KIMI_COMPOSITION_KEYWORDS):
        violations.append(f"{path}: 'type' mixed with a composition keyword")
    if "enum" in schema and not has_type:
        violations.append(f"{path}: 'enum' without 'type'")
    if "required" in schema and not has_type:
        violations.append(f"{path}: 'required' without 'type'")
    if ("oneOf" in schema or "anyOf" in schema) and not has_type:
        violations.append(f"{path}: composition branch list without 'type'")
    properties = schema.get("properties")
    if isinstance(properties, dict):
        for name, sub in cast("dict[str, object]", properties).items():
            violations.extend(_kimi_schema_violations(sub, f"{path}.properties.{name}"))
    for key in _KIMI_COMPOSITION_KEYWORDS:
        seq = schema.get(key)
        if isinstance(seq, list):
            for index, sub in enumerate(cast("list[object]", seq)):
                violations.extend(_kimi_schema_violations(sub, f"{path}.{key}[{index}]"))
        elif isinstance(seq, dict):
            violations.extend(_kimi_schema_violations(seq, f"{path}.{key}"))
    items = schema.get("items")
    if isinstance(items, dict):
        violations.extend(_kimi_schema_violations(items, f"{path}.items"))
    return violations


def _assert_kimi_relayable_schema(server_name: str, tool: UpstreamTool) -> None:
    flattened = normalize_schema_for_function_calling(tool.input_schema)
    violations = _kimi_schema_violations(tool.input_schema, "root")
    if violations:
        repair_note = (
            " (repairable by the server-side flavor flatten)"
            if not _kimi_schema_violations(flattened, "root")
            else " (NOT repairable by the server-side flavor flatten)"
        )
        raise AgentTransportProbeError(
            f"Kimi relay would be rejected by Moonshot for server "
            f"'{server_name}' tool '{tool.name}': " + "; ".join(violations) + repair_note
        )


def _validate_mcp_json_and_handshake(
    server: UpstreamMcpServer,
    config_blob: str,
    label: str,
    ralph_url_key: str,
) -> None:
    parsed = _parse_json_obj(config_blob, label)
    mcp_servers = parsed.get("mcpServers")
    if not isinstance(mcp_servers, dict):
        raise AgentTransportProbeError(f"{label} missing 'mcpServers'")
    raw_entry = cast("dict[str, object]", mcp_servers).get(RALPH_MCP_SERVER_NAME)
    if not isinstance(raw_entry, dict):
        raise AgentTransportProbeError(f"{label} missing Ralph entry; injected wiring is broken")
    entry = cast("dict[str, object]", raw_entry)
    ralph_url = entry.get(ralph_url_key)
    if ralph_url != server.url:
        raise AgentTransportProbeError(
            f"{label} Ralph {ralph_url_key}='{ralph_url!r}' does not match server.url"
        )
    if server.url is not None:
        http_handshake(server.url)


def _http_handshake(endpoint: str) -> None:
    if looks_like_legacy_sse_endpoint(endpoint):
        responses = legacy_sse_jsonrpc_exchange(
            endpoint,
            (initialize_request(), initialized_notification(), tools_list_request()),
            timeout_s=30.0,
        )
        initialize_response = responses[0]
        err = initialize_response.get("error")
        if err is not None:
            raise AgentTransportProbeError(f"initialize failed: {err}")
        tools_response = responses[-1]
        err = tools_response.get("error")
        if err is not None:
            raise AgentTransportProbeError(f"tools/list failed: {err}")
        return
    target = parse_http_endpoint(endpoint)
    initialize_payload = initialize_request()
    initialize_response, session_id = post_http_jsonrpc_with_session(
        endpoint, target, initialize_payload
    )
    err = initialize_response.get("error")
    if err is not None:
        raise AgentTransportProbeError(f"initialize failed: {err}")
    initialized_payload = initialized_notification()
    _, session_id = post_http_jsonrpc_with_session(
        endpoint, target, initialized_payload, session_id=session_id
    )
    tools_payload = tools_list_request()
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
        http_handshake(server.url)
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


http_handshake = _http_handshake
server_handshake = _server_handshake
DEFAULT_TRANSPORTS = _DEFAULT_TRANSPORTS
augment_codex_config_with_server = _augment_codex_config_with_server

__all__ = [
    "DEFAULT_TRANSPORTS",
    "AgentProbeReport",
    "AgentTransportProbeError",
    "augment_codex_config_with_server",
    "http_handshake",
    "probe_agent_transports",
    "server_handshake",
]
