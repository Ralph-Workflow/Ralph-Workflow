"""Standalone MCP server exports.

This package separates standalone server startup/shutdown helpers from the rest
of the bridge implementation so callers can either launch an in-process session
bridge or run the dedicated ``ralph-mcp`` HTTP runtime.
"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, cast

from .lifecycle import SessionBridgeLike, shutdown_mcp_server, start_mcp_server

if TYPE_CHECKING:
    from .runtime import build_fastmcp_server, run_standalone_server

__all__ = [
    "SessionBridgeLike",
    "build_fastmcp_server",
    "run_standalone_server",
    "shutdown_mcp_server",
    "start_mcp_server",
]


def __getattr__(name: str) -> object:
    if name in {"build_fastmcp_server", "run_standalone_server"}:
        return cast("object", getattr(import_module("ralph.mcp.server.runtime"), name))
    msg = f"module {__name__!r} has no attribute {name!r}"
    raise AttributeError(msg)
