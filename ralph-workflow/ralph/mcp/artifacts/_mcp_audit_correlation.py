"""McpAuditCorrelation — correlation metadata from the MCP dispatch layer."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.mcp.protocol.capability_mapping import PolicyMode


@dataclass(frozen=True)
class McpAuditCorrelation:
    """Correlation metadata that comes from the MCP dispatch layer."""

    run_id: str | None = None
    generation: int | None = None
    drain: str | None = None
    policy_mode: PolicyMode | None = None


__all__ = ["McpAuditCorrelation"]
