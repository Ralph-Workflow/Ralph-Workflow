"""Dedicated MCP server lifecycle package.

This package isolates bridge lifecycle integration points and standalone
HTTP server bootstrapping from CLI and pipeline modules.
"""

from .lifecycle import SessionBridgeLike, shutdown_mcp_server, start_mcp_server
from .runtime import build_fastmcp_server, run_standalone_server

__all__ = [
    "SessionBridgeLike",
    "build_fastmcp_server",
    "run_standalone_server",
    "shutdown_mcp_server",
    "start_mcp_server",
]
