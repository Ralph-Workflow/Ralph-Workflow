"""Tests for the ralph.mcp.audit_adapter module."""

from __future__ import annotations

from ralph.mcp import audit_adapter
from ralph.mcp.capability_mapping import (
    AccessDeniedCode,
    AccessDecision,
    McpCapability,
    PolicyMode,
    PolicyOutcomeStatus,
)


def test_outcome_from_decision_denied() -> None:
    """Ensure denied access decisions map to denied policy outcomes."""

    decision = AccessDecision.deny("capability blocked", AccessDeniedCode.CAPABILITY_DENIED)
    outcome = audit_adapter.outcome_from_decision(decision)

    assert outcome.status is PolicyOutcomeStatus.DENIED
    assert outcome.reason == "capability blocked"


def test_sink_adapter_records_tool_event() -> None:
    """Audit sink should translate MCP records into Ralph audit records."""

    metadata = audit_adapter.AuditMetadata(
        event_type=audit_adapter.McpAuditEventType.TOOL,
        details="agent tool call",
        correlation=audit_adapter.AuditCorrelation(
            run_id="run-123",
            generation=2,
            drain="development",
            policy_mode=PolicyMode.DEV,
        ),
    )

    record = audit_adapter.McpAuditRecord(
        timestamp_nanos=1_000_000_000,
        session_id="session-123",
        tool_name="read_file",
        decision=AccessDecision.allow(),
        path=None,
        capability=McpCapability.WORKSPACE_READ,
        metadata=metadata,
    )

    adapter = audit_adapter.RalphAuditSinkAdapter()
    adapter.emit(record)

    drained = adapter.drain_records()
    assert len(drained) == 1

    ralph_record = drained[0]
    assert ralph_record.session_id.as_str() == "session-123"
    assert ralph_record.event_type == "tool"
    assert ralph_record.description == "agent tool call"

    correlation = ralph_record.correlation
    assert correlation is not None
    assert correlation.run_id == "run-123"
    assert correlation.policy_mode == "dev"


def test_drain_records_clears_buffer() -> None:
    """Calling drain_records twice should empty the buffer."""

    adapter = audit_adapter.RalphAuditSinkAdapter()
    record = audit_adapter.McpAuditRecord(
        timestamp_nanos=1,
        session_id="session",
        tool_name="read_file",
        decision=AccessDecision.allow(),
        path=None,
        capability=McpCapability.WORKSPACE_READ,
        metadata=audit_adapter.AuditMetadata(),
    )

    adapter.emit(record)
    assert adapter.drain_records()
    assert not adapter.drain_records()
