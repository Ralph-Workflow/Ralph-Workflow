from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

from ralph.mcp.tools.names import RALPH_MCP_SERVER_NAME
from ralph.mcp.transport.common import _load_mcpservers_from_paths
from ralph.mcp.upstream.config import UpstreamMcpServer, normalize_upstream_mcp_servers

_AGY_HOME_SUBDIR = "antigravity-cli"


def _agy_global_config_path() -> Path:
    return Path.home() / ".gemini" / _AGY_HOME_SUBDIR / "mcp_config.json"


def _agy_secondary_config_path() -> Path:
    return Path.home() / ".gemini" / "config" / "mcp_config.json"


def agy_mcp_config(endpoint: str) -> str:
    config_payload = {
        "mcpServers": {
            RALPH_MCP_SERVER_NAME: {
                "serverUrl": endpoint,
            }
        }
    }
    return json.dumps(config_payload, separators=(",", ":"))


def _normalize_agy_server_entry(name: str, entry: object) -> tuple[str, object] | None:
    if name == RALPH_MCP_SERVER_NAME:
        return None
    if not isinstance(entry, Mapping):
        return None
    casted = entry
    if "serverUrl" in casted and "url" not in casted:
        casted = {**casted, "url": casted["serverUrl"]}
    return name, casted


def load_existing_agy_upstream_servers(
    workspace_path: Path | None = None,
) -> tuple[UpstreamMcpServer, ...]:
    return normalize_upstream_mcp_servers(
        _load_mcpservers_from_paths(
            _agy_mcp_config_paths(workspace_path), _normalize_agy_server_entry
        )
    )


def _agy_mcp_config_paths(workspace_path: Path | None) -> tuple[Path, ...]:
    workspace_paths: tuple[Path, ...] = ()
    if workspace_path is not None:
        workspace_paths = (workspace_path / ".agents" / "mcp_config.json",)
    return (
        *workspace_paths,
        _agy_global_config_path(),
        _agy_secondary_config_path(),
    )


__all__ = [
    "agy_mcp_config",
    "load_existing_agy_upstream_servers",
]
