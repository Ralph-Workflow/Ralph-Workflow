"""MCP bridge module.

Provides the bridge between Ralph's phase system and MCP (Model Context Protocol).
Handles artifact submission, tool exposure, and communication with MCP clients.
"""

from __future__ import annotations

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
from ralph.mcp.transport import (
    MCPTransport,
    StdioTransport,
    TransportError,
)

__all__ = [
    "ArtifactError",
    "ArtifactExistsError",
    "ArtifactNotFoundError",
    "BridgeConfig",
    "BridgeError",
    "MCPBridge",
    "MCPTransport",
    "StdioTransport",
    "TransportError",
    "get_artifact",
    "list_artifacts",
    "submit_artifact",
    "update_artifact",
]
