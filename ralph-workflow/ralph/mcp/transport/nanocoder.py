"""Nanocoder-specific MCP transport helpers."""

from __future__ import annotations

import json
import os
import platform
from pathlib import Path
from typing import cast

from ralph.mcp.tools.names import RALPH_MCP_SERVER_NAME, claude_tool_name
from ralph.mcp.transport.common import _load_mcpservers_from_paths
from ralph.mcp.upstream.config import UpstreamMcpServer, normalize_upstream_mcp_servers

_NANOCODER_MCP_ENV = "NANOCODER_MCPSERVERS"
_NANOCODER_CONFIG_DIR_ENV = "NANOCODER_CONFIG_DIR"


def build_nanocoder_mcp_config(
    existing: str | None,
    endpoint: str,
    *,
    always_allow: tuple[str, ...] = (),
) -> tuple[str, tuple[UpstreamMcpServer, ...]]:
    """Build a Nanocoder MCP payload with Ralph injected as the managed server."""
    server_map = _parse_nanocoder_mcp_servers(existing)
    upstreams = normalize_upstream_mcp_servers(server_map)
    ralph_server: dict[str, object] = {
        "transport": "http",
        "url": endpoint,
    }
    if always_allow:
        expanded_allow: list[str] = []
        for tool_name in always_allow:
            if tool_name not in expanded_allow:
                expanded_allow.append(tool_name)
            alias = claude_tool_name(tool_name, server_name=RALPH_MCP_SERVER_NAME)
            if alias not in expanded_allow:
                expanded_allow.append(alias)
        ralph_server["alwaysAllow"] = expanded_allow
    server_map["ralph"] = ralph_server
    payload = {"mcpServers": server_map}
    return json.dumps(payload, sort_keys=True), upstreams


def load_existing_nanocoder_upstream_servers(
    workspace_path: Path | None,
    *,
    env: dict[str, str] | None = None,
) -> tuple[UpstreamMcpServer, ...]:
    """Load Nanocoder MCP servers from documented config locations."""
    active_env = env if env is not None else cast("dict[str, str]", os.environ)
    config_dir = active_env.get(_NANOCODER_CONFIG_DIR_ENV)
    paths: tuple[Path, ...]
    if config_dir:
        paths = (Path(config_dir).expanduser() / ".mcp.json",)
    else:
        paths = _nanocoder_mcp_config_paths(workspace_path)
    return normalize_upstream_mcp_servers(_load_mcpservers_from_paths(paths))


def _nanocoder_mcp_config_paths(workspace_path: Path | None) -> tuple[Path, ...]:
    paths: list[Path] = []
    if workspace_path is not None:
        paths.append(workspace_path / ".mcp.json")
    paths.append(_global_nanocoder_config_dir() / ".mcp.json")
    return tuple(paths)


def _global_nanocoder_config_dir() -> Path:
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / "nanocoder"
    if platform.system() == "Darwin":
        return Path.home() / "Library" / "Preferences" / "nanocoder"
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg) / "nanocoder"
    return Path.home() / ".config" / "nanocoder"


def _parse_nanocoder_mcp_servers(existing: str | None) -> dict[str, object]:
    if not existing:
        return {}
    try:
        decoded: object = json.loads(existing)
    except json.JSONDecodeError:
        return {}

    if isinstance(decoded, list):
        mapped: dict[str, object] = {}
        for item in decoded:
            if not isinstance(item, dict):
                continue
            item_map = cast("dict[str, object]", item)
            name = item_map.get("name")
            if not isinstance(name, str) or not name:
                continue
            mapped[name] = {key: value for key, value in item_map.items() if key != "name"}
        return mapped

    if not isinstance(decoded, dict):
        return {}
    wrapped = decoded.get("mcpServers")
    if isinstance(wrapped, dict):
        return cast("dict[str, object]", wrapped)
    return {}


__all__ = [
    "build_nanocoder_mcp_config",
    "load_existing_nanocoder_upstream_servers",
]
