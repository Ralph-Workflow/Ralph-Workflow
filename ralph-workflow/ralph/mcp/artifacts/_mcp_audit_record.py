"""McpAuditRecord — audit record emitted by the MCP server dispatch layer."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ralph.mcp.artifacts._audit_metadata import AuditMetadata

if TYPE_CHECKING:
    from ralph.mcp.protocol.capability_mapping import AccessDecision, McpCapability


@dataclass
class McpAuditRecord:
    """Audit record emitted by the MCP server dispatch layer."""

    timestamp_nanos: int
    session_id: str
    tool_name: str
    decision: AccessDecision
    path: str | None = None
    capability: McpCapability | None = None
    metadata: AuditMetadata = field(default_factory=AuditMetadata)


__all__ = ["McpAuditRecord"]
