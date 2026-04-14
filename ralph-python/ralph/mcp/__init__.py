"""MCP bridge module.

Provides the bridge between Ralph's phase system and MCP (Model Context Protocol).
Handles artifact submission, tool exposure, and communication with MCP clients.
"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from types import ModuleType

from ralph.mcp.artifacts import (
    ArtifactError,
    ArtifactExistsError,
    ArtifactNotFoundError,
    get_artifact,
    list_artifacts,
    submit_artifact,
    update_artifact,
)
from ralph.mcp.bridge import (
    BridgeConfig,
    BridgeError,
    MCPBridge,
)
from ralph.mcp.startup import (
    HeartbeatPolicy,
    SessionBridgeError,
    access_mode_for_drain,
    start_mcp_server_for_session,
)
from ralph.mcp.transport import (
    MCPTransport,
    StdioTransport,
    TransportError,
)

if TYPE_CHECKING:
    # Type aliases that are loaded lazily via __getattr__
    ToolBridge: type
    ToolBridgeError: type
    ToolDefinition: type
    ToolMetadata: type


__all__ = [
    "ArtifactError",
    "ArtifactExistsError",
    "ArtifactNotFoundError",
    "BridgeConfig",
    "BridgeError",
    "HeartbeatPolicy",
    "MCPBridge",
    "MCPTransport",
    "SessionBridgeError",
    "StdioTransport",
    "ToolBridge",
    "ToolBridgeError",
    "ToolDefinition",
    "ToolMetadata",
    "TransportError",
    "access_mode_for_drain",
    "get_artifact",
    "list_artifacts",
    "start_mcp_server_for_session",
    "submit_artifact",
    "update_artifact",
]


_TOOL_BRIDGE_SYMBOLS = {
    "ToolBridge",
    "ToolBridgeError",
    "ToolDefinition",
    "ToolMetadata",
}


def __getattr__(name: str) -> object:
    if name in _TOOL_BRIDGE_SYMBOLS:
        module: ModuleType = import_module(".tool_bridge", __name__)
        value = cast("object", getattr(module, name))
        module_globals = cast("dict[str, object]", globals())
        module_globals[name] = value
        return value
    raise AttributeError(f"module {__name__} has no attribute {name}")
