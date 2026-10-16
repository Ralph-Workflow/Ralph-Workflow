"""Standalone MCP server exports.

This package separates standalone server startup/shutdown helpers from the rest
of the bridge implementation so callers can either launch an in-process session
bridge or run the dedicated ``ralph-mcp`` HTTP runtime.
"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, cast

from ._activity_sink import reset_subagent_sink, set_subagent_sink

if TYPE_CHECKING:
    from .lifecycle import McpServerExtras, SessionBridgeLike, shutdown_mcp_server, start_mcp_server
    from .runtime import run_standalone_server

__all__ = [
    "McpServerExtras",
    "SessionBridgeLike",
    "factory_impl",
    "reset_subagent_sink",
    "run_standalone_server",
    "set_subagent_sink",
    "shutdown_mcp_server",
    "start_mcp_server",
]


_LAZY_MODULES = {
    "factory_impl": "ralph.mcp.server.factory_impl",
    "run_standalone_server": "ralph.mcp.server.runtime",
    "start_mcp_server": "ralph.mcp.server.lifecycle",
    "shutdown_mcp_server": "ralph.mcp.server.lifecycle",
    "McpServerExtras": "ralph.mcp.server.lifecycle",
    "SessionBridgeLike": "ralph.mcp.server.lifecycle",
}


def __getattr__(name: str) -> object:
    target = _LAZY_MODULES.get(name)
    if target is not None:
        # Lazy: an eager lifecycle import here creates a cycle
        # (upstream.config -> server._mcp_server_error -> package init).
        return cast("object", getattr(import_module(target), name))
    try:
        return import_module(f"ralph.mcp.server.{name}")
    except ImportError:
        pass
    msg = f"module {__name__!r} has no attribute {name!r}"
    raise AttributeError(msg)
