"""Capability-denied MCP tool error."""

from __future__ import annotations

from .tool_error import ToolError


class CapabilityDeniedError(ToolError):
    """Raised when a required session capability is not available."""
