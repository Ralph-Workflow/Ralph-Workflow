"""Nanocoder-specific MCP transport helpers."""

from __future__ import annotations

import json
import os
import platform
from pathlib import Path
from typing import TYPE_CHECKING, cast

from ralph.mcp.tool_contract import expand_tool_names_with_aliases
from ralph.mcp.tools.names import RALPH_MCP_SERVER_NAME
from ralph.mcp.transport.common import _load_mcpservers_from_paths, merge_existing_upstreams
from ralph.mcp.upstream.config import UpstreamMcpServer, normalize_upstream_mcp_servers

if TYPE_CHECKING:
    from collections.abc import Mapping


_NANOCODER_MCP_ENV = "NANOCODER_MCPSERVERS"
_NANOCODER_CONFIG_DIR_ENV = "NANOCODER_CONFIG_DIR"


def build_nanocoder_mcp_config(
    existing: str | None,
    endpoint: str,
    *,
    always_allow: tuple[str, ...] = (),
    unsafe_mode: bool = False,
    workspace_path: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> tuple[str, tuple[UpstreamMcpServer, ...]]:
    """Build a Nanocoder MCP payload with Ralph injected as the managed server."""
    agent_servers = _parse_nanocoder_mcp_servers(existing)
    upstreams = normalize_upstream_mcp_servers(agent_servers)
    ralph_server: dict[str, object] = {
        "transport": "http",
        "url": endpoint,
    }
    if always_allow:
        ralph_server["alwaysAllow"] = expand_tool_names_with_aliases(always_allow)
    if unsafe_mode:
        file_servers = _load_mcpservers_from_paths(
            _nanocoder_mcp_config_paths(workspace_path, env=env)
        )
        mcp_servers = {**agent_servers, **file_servers}
        current_config: dict[str, object] = {
            "mcpServers": mcp_servers,
            "workspace_path": workspace_path,
        }
        merged_config = merge_existing_upstreams(
            "nanocoder", current_config, unsafe_mode=True, workspace_path=workspace_path
        )
        merged_servers = dict(
            cast("dict[str, object]", merged_config.get("mcpServers", {}))
        )
    else:
        merged_servers = dict(agent_servers)
    merged_servers["ralph"] = ralph_server
    payload = {"mcpServers": merged_servers}
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
        paths = _nanocoder_mcp_config_paths(workspace_path, env=active_env)
    servers = _load_mcpservers_from_paths(paths)
    servers.pop(RALPH_MCP_SERVER_NAME, None)
    return normalize_upstream_mcp_servers(servers)


def _nanocoder_mcp_config_paths(
    workspace_path: Path | None, *, env: Mapping[str, str] | None = None
) -> tuple[Path, ...]:
    active_env: Mapping[str, str] = env if env is not None else os.environ
    config_dir = active_env.get(_NANOCODER_CONFIG_DIR_ENV)
    paths: list[Path] = []
    if config_dir:
        paths.append(Path(config_dir).expanduser() / ".mcp.json")
    else:
        if workspace_path is not None:
            paths.append(workspace_path / ".mcp.json")
        paths.append(_global_nanocoder_config_dir(env=active_env) / ".mcp.json")
    return tuple(paths)


def _global_nanocoder_config_dir(env: Mapping[str, str] | None = None) -> Path:
    active_env: Mapping[str, str] = env if env is not None else os.environ
    appdata = active_env.get("APPDATA")
    if appdata:
        return Path(appdata) / "nanocoder"
    home = Path(active_env.get("HOME") or str(Path.home()))
    if platform.system() == "Darwin":
        return home / "Library" / "Preferences" / "nanocoder"
    xdg = active_env.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg) / "nanocoder"
    return home / ".config" / "nanocoder"


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
