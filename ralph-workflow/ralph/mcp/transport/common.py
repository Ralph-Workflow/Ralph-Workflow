"""Shared MCP transport helpers: mcp.toml loading, upstream merging, env serialization."""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

from ralph.config.mcp_loader import load_mcp_config
from ralph.mcp.upstream.config import (
    UPSTREAM_MCP_CONFIG_ENV,
    UpstreamMcpServer,
    serialize_upstream_mcp_servers,
)

if TYPE_CHECKING:
    from pathlib import Path


def mcp_toml_as_upstreams(workspace_path: Path | None) -> tuple[UpstreamMcpServer, ...]:
    config_path = (workspace_path / ".agent" / "mcp.toml") if workspace_path is not None else None
    mcp_config = load_mcp_config(config_path=config_path)
    return tuple(
        UpstreamMcpServer(
            name=spec.name,
            transport=spec.transport,
            url=spec.url,
            command=spec.command,
            args=tuple(spec.args),
            env=dict(spec.env),
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


__all__ = [
    "mcp_toml_as_upstreams",
    "merge_mcp_toml_into_upstreams",
    "set_upstream_mcp_config",
]
