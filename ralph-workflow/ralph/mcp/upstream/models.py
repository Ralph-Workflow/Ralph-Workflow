"""Data models shared across the upstream MCP client subsystem.

Contains ``UpstreamTool`` (description of a single tool advertised by an upstream
server) and ``UpstreamCallError`` (raised when a remote tool call or server
reachability check fails).
"""

from __future__ import annotations

from ralph.mcp.upstream.upstream_tool import UpstreamTool


class UpstreamCallError(Exception):
    """Raised when a remote tool call or upstream server reachability check fails."""


__all__ = [
    "UpstreamCallError",
    "UpstreamTool",
]
