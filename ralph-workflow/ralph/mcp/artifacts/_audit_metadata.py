"""AuditMetadata — extended metadata attached to an MCP audit record."""

from __future__ import annotations

from dataclasses import dataclass, field

from ralph.mcp.artifacts._mcp_audit_correlation import McpAuditCorrelation
from ralph.mcp.artifacts._mcp_audit_event_type import McpAuditEventType


@dataclass
class AuditMetadata:
    """Extended metadata attached to an MCP audit record."""

    event_type: McpAuditEventType = McpAuditEventType.TOOL
    details: str | None = None
    correlation: McpAuditCorrelation = field(default_factory=McpAuditCorrelation)


__all__ = ["AuditMetadata"]
