"""RalphAuditRecord — audit record format consumed by Ralph's audit trail."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.mcp.artifacts._agent_session_id import AgentSessionId
    from ralph.mcp.artifacts._audit_correlation import AuditCorrelation
    from ralph.mcp.protocol.capability_mapping import Capability as RalphCapability
    from ralph.mcp.protocol.capability_mapping import PolicyOutcome


@dataclass(frozen=True)
class RalphAuditRecord:
    """Audit record format consumed by Ralph's audit trail."""

    session_id: AgentSessionId
    timestamp: int
    capability: RalphCapability
    outcome: PolicyOutcome
    description: str
    duration_ms: int | None = None
    result_status: str | None = None
    event_type: str | None = None
    correlation: AuditCorrelation | None = None


__all__ = ["RalphAuditRecord"]
