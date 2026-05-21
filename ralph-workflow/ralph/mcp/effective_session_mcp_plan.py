"""Canonical effective MCP inventory for one agent session."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ralph.mcp.tools.names import RALPH_MCP_SERVER_NAME

if TYPE_CHECKING:
    from ralph.mcp.upstream.config import UpstreamMcpServer


@dataclass(frozen=True)
class EffectiveSessionMcpPlan:
    """Canonical effective MCP inventory for one agent session."""

    custom_servers: tuple[UpstreamMcpServer, ...]
    agent_upstream_servers: tuple[UpstreamMcpServer, ...]
    effective_servers: tuple[UpstreamMcpServer, ...]
    provider_visible_server_names: tuple[str, ...] = (RALPH_MCP_SERVER_NAME,)
