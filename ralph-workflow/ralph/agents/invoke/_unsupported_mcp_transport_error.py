"""UnsupportedMcpTransportError — raised for unsupported MCP transport types."""

from __future__ import annotations


class UnsupportedMcpTransportError(RuntimeError):
    """Raised when MCP-backed execution is requested for an unsupported transport."""


__all__ = ["UnsupportedMcpTransportError"]
