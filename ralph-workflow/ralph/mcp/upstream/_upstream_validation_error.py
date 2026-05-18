"""Error raised in strict mode when upstream MCP servers fail validation."""

from __future__ import annotations


class UpstreamValidationError(RuntimeError):
    """Raised in strict mode when one or more upstream MCP servers fail validation."""
