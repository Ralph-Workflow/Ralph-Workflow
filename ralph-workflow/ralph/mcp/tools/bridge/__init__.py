"""MCP tool registry and handler dispatch.

This module ports the Rust `mcp_server::tool_bridge` registry layer into Python.
It owns tool metadata registration, duplicate protection, lookup, and dispatch.
The default registry builder mirrors the Rust bridge by registering lazy handler
wrappers for the Ralph MCP tool modules.
"""

from __future__ import annotations

from ralph.mcp.tools.bridge._lazy_tool_handler import LazyToolHandler
from ralph.mcp.tools.bridge._registered_tool import RegisteredTool
from ralph.mcp.tools.bridge._registration_handler import RegistrationHandler
from ralph.mcp.tools.bridge._registry import build_ralph_tool_registry, tool_specs
from ralph.mcp.tools.bridge._tool_bridge import ToolBridge
from ralph.mcp.tools.bridge._tool_bridge_error import ToolBridgeError
from ralph.mcp.tools.bridge._tool_definition import ToolDefinition
from ralph.mcp.tools.bridge._tool_dispatch_error import ToolDispatchError
from ralph.mcp.tools.bridge._tool_metadata import ToolMetadata
from ralph.mcp.tools.bridge._tool_registration_error import ToolRegistrationError
from ralph.mcp.tools.bridge._tool_spec import ToolSpec
from ralph.mcp.tools.bridge._upstream_proxy_handler import UpstreamProxyHandler

__all__ = [
    "LazyToolHandler",
    "RegisteredTool",
    "RegistrationHandler",
    "ToolBridge",
    "ToolBridgeError",
    "ToolDefinition",
    "ToolDispatchError",
    "ToolMetadata",
    "ToolRegistrationError",
    "ToolSpec",
    "UpstreamProxyHandler",
    "build_ralph_tool_registry",
    "tool_specs",
]
