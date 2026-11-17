from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from ralph.config.mcp_loader import load_mcp_config
from ralph.mcp.protocol._object_dict import as_object_dict
from ralph.mcp.tools.names import RALPH_MCP_SERVER_NAME
from ralph.mcp.upstream.config import (
    UPSTREAM_MCP_CONFIG_ENV,
    UpstreamMcpServer,
    normalize_upstream_mcp_servers,
    serialize_upstream_mcp_servers,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from ralph.config.mcp_models import McpConfig


def mcp_toml_as_upstreams(workspace_path: Path | None) -> tuple[UpstreamMcpServer, ...]:
    config_path = (workspace_path / ".agent" / "mcp.toml") if workspace_path is not None else None
    mcp_config = load_mcp_config(config_path=config_path)
    return mcp_config_as_upstreams(mcp_config)


def _parse_json_config_file(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    try:
        raw_payload: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return as_object_dict(raw_payload) or {}


def _load_mcpservers_from_paths(
    paths: tuple[Path, ...],
    entry_normalizer: Callable[[str, object], tuple[str, object] | None] | None = None,
) -> dict[str, object]:
    merged: dict[str, object] = {}
    for path in paths:
        config_obj = _parse_json_config_file(path)
        if not config_obj:
            continue
        server_entries = as_object_dict(config_obj.get("mcpServers"))
        if server_entries is None:
            continue
        if entry_normalizer is None:
            merged.update(server_entries)
            continue
        for server_name, server_entry in server_entries.items():
            normalized = entry_normalizer(server_name, server_entry)
            if normalized is not None:
                merged[normalized[0]] = normalized[1]
    return merged


def mcp_config_as_upstreams(mcp_config: McpConfig) -> tuple[UpstreamMcpServer, ...]:
    return tuple(
        UpstreamMcpServer(
            name=spec.name,
            transport=spec.transport,
            url=spec.url,
            command=spec.command,
            args=tuple(spec.args),
            env=dict(spec.env),
            origin="custom",
        )
        for spec in mcp_config.mcp_servers.values()
    )


def merge_mcp_toml_into_upstreams(
    agent_native: tuple[UpstreamMcpServer, ...],
    mcp_toml_servers: tuple[UpstreamMcpServer, ...],
) -> tuple[UpstreamMcpServer, ...]:
    merged: dict[str, UpstreamMcpServer] = {s.name: s for s in agent_native}
    for server in mcp_toml_servers:
        if server.name in merged:
            logger.warning(
                "mcp.toml server '{}' overrides agent-native upstream config",
                server.name,
            )
        merged[server.name] = server
    return tuple(merged.values())


def set_upstream_mcp_config(
    runtime_env: dict[str, str], upstreams: tuple[UpstreamMcpServer, ...]
) -> None:
    if upstreams:
        runtime_env[UPSTREAM_MCP_CONFIG_ENV] = serialize_upstream_mcp_servers(upstreams)
        return
    runtime_env.pop(UPSTREAM_MCP_CONFIG_ENV, None)


def _load_opencode_mcp_servers_from_current_config(
    current_config: dict[str, object],
) -> dict[str, object]:
    return as_object_dict(current_config.get("mcp")) or {}


def _upstream_as_dict(s: UpstreamMcpServer, *, server_url_key: bool = False) -> dict[str, object]:
    result: dict[str, object] = {
        "name": s.name,
        "transport": s.transport if s.transport else "http",
        "serverUrl" if server_url_key else "url": s.url,
    }
    if s.command:
        result["command"] = s.command
    if s.args:
        result["args"] = list(s.args)
    if s.env:
        result["env"] = dict(s.env)
    return result


def _merge_opencode(current_config: dict[str, object], *, unsafe_mode: bool) -> dict[str, object]:
    ralph_opencode_mcp = as_object_dict(current_config.get("mcp")) or {}
    ralph_opencode = ralph_opencode_mcp.get(RALPH_MCP_SERVER_NAME)
    if not unsafe_mode:
        if ralph_opencode is not None:
            return {"mcp": {RALPH_MCP_SERVER_NAME: ralph_opencode}}
        return {"mcp": {}}
    existing_native = _load_opencode_mcp_servers_from_current_config(current_config)
    merged: dict[str, object] = dict(existing_native)
    if ralph_opencode is not None:
        merged[RALPH_MCP_SERVER_NAME] = ralph_opencode
    return {"mcp": merged}


def _merge_default(
    agent_name: str,
    current_config: dict[str, object],
    *,
    unsafe_mode: bool,
    workspace_path: object = None,
) -> dict[str, object]:
    ralph_mcp_servers = as_object_dict(current_config.get("mcpServers")) or {}
    ralph_entry = ralph_mcp_servers.get(RALPH_MCP_SERVER_NAME)
    if not unsafe_mode:
        if ralph_entry is not None:
            return {"mcpServers": {RALPH_MCP_SERVER_NAME: ralph_entry}}
        return {}
    upstreams = _load_upstreams_for_agent(agent_name, current_config, workspace_path)
    filtered_upstreams = tuple(s for s in upstreams if s.name != RALPH_MCP_SERVER_NAME)
    existing_map: dict[str, object] = {
        s.name: _upstream_as_dict(s, server_url_key=(agent_name == "agy"))
        for s in filtered_upstreams
    }
    merged = dict(existing_map)
    if ralph_entry is not None:
        merged[RALPH_MCP_SERVER_NAME] = ralph_entry
    return {"mcpServers": merged}


def merge_existing_upstreams(
    agent_name: str,
    current_config: dict[str, object],
    *,
    unsafe_mode: bool,
    workspace_path: object = None,
) -> dict[str, object]:
    if agent_name == "opencode":
        return _merge_opencode(current_config, unsafe_mode=unsafe_mode)
    return _merge_default(
        agent_name,
        current_config,
        unsafe_mode=unsafe_mode,
        workspace_path=workspace_path,
    )


def _load_upstreams_for_agent(
    agent_name: str,
    current_config: dict[str, object],
    workspace_path: object = None,
) -> tuple[UpstreamMcpServer, ...]:
    from ralph.mcp.transport import agy as agy_transport
    from ralph.mcp.transport import claude as claude_transport
    from ralph.mcp.transport import cursor as cursor_transport
    from ralph.mcp.transport import kimi as kimi_transport
    from ralph.mcp.transport import nanocoder as nanocoder_transport

    _workspace_path = (
        workspace_path if workspace_path is not None else current_config.get("workspace_path")
    )
    workspace = _workspace_path if isinstance(_workspace_path, Path) else None
    existing_mcp_servers = as_object_dict(current_config.get("mcpServers"))
    if agent_name in ("claude", "agy", "nanocoder", "cursor", "kimi"):
        servers: dict[str, object] = {}
        if existing_mcp_servers is not None and len(existing_mcp_servers) > 1:
            servers = existing_mcp_servers
        elif agent_name == "claude":
            return claude_transport.load_existing_claude_upstream_servers(workspace)
        elif agent_name == "agy":
            paths = agy_transport._agy_mcp_config_paths(workspace)
            servers = _load_mcpservers_from_paths(paths, agy_transport._normalize_agy_server_entry)
        elif agent_name == "cursor":
            paths = cursor_transport._cursor_paths_to_consider(workspace)
            servers = _load_mcpservers_from_paths(
                paths, cursor_transport._normalize_cursor_server_entry
            )
        elif agent_name == "kimi":
            paths = kimi_transport._kimi_paths_to_consider(workspace)
            servers = _load_mcpservers_from_paths(
                paths, kimi_transport._normalize_kimi_server_entry
            )
        else:
            paths = nanocoder_transport._nanocoder_mcp_config_paths(workspace)
            servers = _load_mcpservers_from_paths(paths)
        servers.pop(RALPH_MCP_SERVER_NAME, None)
        return normalize_upstream_mcp_servers(servers)
    return ()


__all__ = [
    "mcp_config_as_upstreams",
    "mcp_toml_as_upstreams",
    "merge_existing_upstreams",
    "merge_mcp_toml_into_upstreams",
    "set_upstream_mcp_config",
]
