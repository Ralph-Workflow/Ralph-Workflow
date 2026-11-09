"""Public MCP transport and tool-bridge package.

This package exposes transport abstractions, tool registration, and access-mode
helpers used by both the pipeline and standalone ``ralph-mcp`` runtime.

Public names resolve lazily, on first attribute access, from the module that
defines them. Importing a leaf submodule such as ``ralph.mcp.artifacts`` no
longer pulls the protocol startup module (and through it ``httpx`` and
``rich``) into the importing process, which keeps every interpreter that
touches MCP artifacts cheap to start.
"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from ralph.mcp.artifacts._artifact_error import ArtifactError
    from ralph.mcp.protocol.startup import (
        HeartbeatPolicy,
        SessionBridgeError,
        access_mode_for_drain,
    )
    from ralph.mcp.protocol.transport import (
        MCPTransport,
        StdioTransport,
        TransportError,
    )
    from ralph.mcp.tools.bridge import (
        ToolBridge,
        ToolBridgeError,
        ToolDefinition,
        ToolMetadata,
    )

__all__ = [
    "ArtifactError",
    "HeartbeatPolicy",
    "MCPTransport",
    "SessionBridgeError",
    "StdioTransport",
    "ToolBridge",
    "ToolBridgeError",
    "ToolDefinition",
    "ToolMetadata",
    "TransportError",
    "access_mode_for_drain",
]

#: Public name -> (defining module, attribute name in that module).
_LAZY_EXPORTS: dict[str, tuple[str, str]] = {
    "ArtifactError": ("ralph.mcp.artifacts._artifact_error", "ArtifactError"),
    "HeartbeatPolicy": ("ralph.mcp.protocol.startup", "HeartbeatPolicy"),
    "MCPTransport": ("ralph.mcp.protocol.transport", "MCPTransport"),
    "SessionBridgeError": ("ralph.mcp.protocol.startup", "SessionBridgeError"),
    "StdioTransport": ("ralph.mcp.protocol.transport", "StdioTransport"),
    "ToolBridge": ("ralph.mcp.tools.bridge", "ToolBridge"),
    "ToolBridgeError": ("ralph.mcp.tools.bridge", "ToolBridgeError"),
    "ToolDefinition": ("ralph.mcp.tools.bridge", "ToolDefinition"),
    "ToolMetadata": ("ralph.mcp.tools.bridge", "ToolMetadata"),
    "TransportError": ("ralph.mcp.protocol.transport", "TransportError"),
    "access_mode_for_drain": ("ralph.mcp.protocol.startup", "access_mode_for_drain"),
}


def __getattr__(name: str) -> object:
    """Resolve a public export from its defining module on first access.

    The eager imports this replaces pulled ``ralph.mcp.protocol.startup`` --
    and through it ``httpx`` and ``rich`` -- into every process that only
    needed an MCP leaf submodule.
    """
    try:
        module_name, attr_name = _LAZY_EXPORTS[name]
    except KeyError as exc:
        msg = f"module {__name__!r} has no attribute {name!r}"
        raise AttributeError(msg) from exc

    module = import_module(module_name)
    namespace = cast("dict[str, object]", module.__dict__)
    value = namespace[attr_name]
    cast("dict[str, object]", globals())[name] = value
    return value
