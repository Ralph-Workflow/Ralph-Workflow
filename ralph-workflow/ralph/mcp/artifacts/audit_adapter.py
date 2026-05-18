"""Audit adapter utilities for MCP records."""

from __future__ import annotations

from threading import Lock
from typing import TYPE_CHECKING, Protocol

from ralph.mcp.artifacts._agent_session_id import AgentSessionId
from ralph.mcp.artifacts._audit_correlation import AuditCorrelation
from ralph.mcp.artifacts._audit_metadata import AuditMetadata
from ralph.mcp.artifacts._mcp_audit_correlation import McpAuditCorrelation
from ralph.mcp.artifacts._mcp_audit_event_type import McpAuditEventType
from ralph.mcp.artifacts._mcp_audit_record import McpAuditRecord
from ralph.mcp.artifacts._ralph_audit_record import RalphAuditRecord
from ralph.mcp.protocol.capability_mapping import (
    AccessDecision,
    PolicyMode,
    PolicyOutcome,
    PolicyOutcomeStatus,
    lookup_ralph_capability,
)
from ralph.mcp.protocol.capability_mapping import (
    Capability as RalphCapability,
)

if TYPE_CHECKING:

    class AuditSink(Protocol):
        """Protocol defining the MCP audit sink contract."""

        def emit(self, record: McpAuditRecord) -> None: ...

        def flush(self) -> None: ...


def outcome_from_decision(decision: AccessDecision) -> PolicyOutcome:
    """Convert an access decision into a policy outcome."""

    if decision.is_allowed():
        return PolicyOutcome(status=PolicyOutcomeStatus.APPROVED)
    reason = decision.reason or "denied"
    return PolicyOutcome(status=PolicyOutcomeStatus.DENIED, reason=reason)


def resolve_audit_capability(record: McpAuditRecord) -> RalphCapability:
    """Map MCP capability to Ralph capability or fall back to workspace read."""

    if record.capability is None:
        return RalphCapability.WORKSPACE_READ
    mapped = lookup_ralph_capability(record.capability)
    return mapped or RalphCapability.WORKSPACE_READ


def event_type_label(event_type: McpAuditEventType) -> str:
    """Return the lowercase label for an MCP audit event."""

    return event_type.value


def default_description(record: McpAuditRecord) -> str:
    """Build a default description when no metadata details are provided."""

    if record.decision.is_allowed():
        return f"MCP tool '{record.tool_name}' executed successfully"
    reason = record.decision.reason or "denied"
    return f"MCP tool '{record.tool_name}' access denied: {reason}"


def resolve_description(record: McpAuditRecord) -> str:
    """Prefer metadata details but fall back to the default description."""

    return record.metadata.details or default_description(record)


def resolve_correlation(record: McpAuditRecord) -> AuditCorrelation | None:
    """Construct correlation metadata for Ralph audit records."""

    corr = record.metadata.correlation
    policy_mode = None
    if corr.policy_mode is not None:
        policy_mode = (
            corr.policy_mode.value
            if isinstance(corr.policy_mode, PolicyMode)
            else str(corr.policy_mode)
        )
    if not any([corr.run_id, corr.generation, corr.drain, policy_mode]):
        return None
    return AuditCorrelation(
        run_id=corr.run_id,
        generation=corr.generation,
        drain=corr.drain,
        policy_mode=policy_mode,
    )


def to_ralph_record(record: McpAuditRecord) -> RalphAuditRecord:
    """Translate an MCP audit record into Ralph's domain model."""

    return RalphAuditRecord(
        session_id=AgentSessionId.from_string(record.session_id),
        timestamp=record.timestamp_nanos // 1_000_000_000,
        capability=resolve_audit_capability(record),
        outcome=outcome_from_decision(record.decision),
        description=resolve_description(record),
        event_type=event_type_label(record.metadata.event_type),
        correlation=resolve_correlation(record),
    )


class RalphAuditSinkAdapter:
    """Adapter that buffers Ralph audit records produced by MCP."""

    def __init__(self) -> None:
        self._records: list[RalphAuditRecord] = []
        self._lock = Lock()

    def emit(self, record: McpAuditRecord) -> None:
        """Store a converted audit record in the buffer."""

        with self._lock:
            self._records.append(to_ralph_record(record))

    def drain_records(self) -> list[RalphAuditRecord]:
        """Return buffered records and clear the buffer."""

        with self._lock:
            drained = list(self._records)
            self._records.clear()
        return drained

    def flush(self) -> None:
        """No-op flush since records are held in memory."""


__all__ = [
    "AgentSessionId",
    "AuditCorrelation",
    "AuditMetadata",
    "AuditSink",
    "McpAuditCorrelation",
    "McpAuditEventType",
    "McpAuditRecord",
    "RalphAuditRecord",
    "RalphAuditSinkAdapter",
    "outcome_from_decision",
    "resolve_audit_capability",
]
