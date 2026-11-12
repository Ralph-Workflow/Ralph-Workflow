from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Final, cast

from ralph.config._agent_overrides import agent_environment_value
from ralph.mcp.tools.names import RALPH_MCP_SERVER_NAME
from ralph.mcp.transport.common import _load_mcpservers_from_paths
from ralph.mcp.upstream.config import UpstreamMcpServer, normalize_upstream_mcp_servers
from ralph.timeout_defaults import EXEC_SOFT_REPLY_BUDGET_MS

_KIMI_TOOL_TIMEOUT_HEADROOM_MS: Final[int] = 30_000

_KIMI_TOOL_TIMEOUT_MS: Final[int] = EXEC_SOFT_REPLY_BUDGET_MS + _KIMI_TOOL_TIMEOUT_HEADROOM_MS


def _kimi_global_config_path() -> Path:
    kimi_home = agent_environment_value("KIMI_CODE_HOME") or ""
    home = Path(kimi_home) if kimi_home else Path.home() / ".kimi-code"
    return home / "mcp.json"


def _kimi_workspace_config_path(workspace_path: Path) -> Path:
    return workspace_path / ".kimi-code" / "mcp.json"


def kimi_mcp_config(endpoint: str) -> str:
    config_payload = {
        "mcpServers": {
            RALPH_MCP_SERVER_NAME: {
                "url": endpoint,
                "toolTimeoutMs": _KIMI_TOOL_TIMEOUT_MS,
            }
        }
    }
    return json.dumps(config_payload, separators=(",", ":"))


def _kimi_paths_to_consider(
    workspace_path: Path | None,
) -> tuple[Path, ...]:
    workspace_paths: tuple[Path, ...] = ()
    if workspace_path is not None:
        workspace_paths = (_kimi_workspace_config_path(workspace_path),)
    return (
        _kimi_global_config_path(),
        *workspace_paths,
    )


def _normalize_kimi_server_entry(name: str, entry: object) -> tuple[str, object] | None:
    if name == RALPH_MCP_SERVER_NAME:
        return None
    if not isinstance(entry, Mapping):
        return None
    return name, cast("dict[str, object]", entry)


def load_existing_kimi_upstream_servers(
    workspace_path: Path | None = None,
) -> tuple[UpstreamMcpServer, ...]:
    return normalize_upstream_mcp_servers(
        _load_mcpservers_from_paths(
            _kimi_paths_to_consider(workspace_path),
            _normalize_kimi_server_entry,
        )
    )


__all__ = [
    "kimi_mcp_config",
    "load_existing_kimi_upstream_servers",
]
