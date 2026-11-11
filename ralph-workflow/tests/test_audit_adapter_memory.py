from __future__ import annotations

from ralph.mcp.artifacts import audit_adapter
from ralph.mcp.protocol.capability_mapping import (
    AccessDecision,
    McpCapability,
    PolicyMode,
)


def _make_record(session_id: str, tool_name: str, run_id: str) -> audit_adapter.McpAuditRecord:
    metadata = audit_adapter.AuditMetadata(
        event_type=audit_adapter.McpAuditEventType.TOOL,
        details=f"tool call for {tool_name}",
        correlation=audit_adapter.McpAuditCorrelation(
            run_id=run_id,
            generation=0,
            drain="development",
            policy_mode=PolicyMode.DEVELOPMENT,
        ),
    )
    return audit_adapter.McpAuditRecord(
        timestamp_nanos=1_000_000_000,
        session_id=session_id,
        tool_name=tool_name,
        decision=AccessDecision.allow(),
        path=None,
        capability=McpCapability.WORKSPACE_READ,
        metadata=metadata,
    )


def test_records_bounded_by_fifo_cap() -> None:
    adapter = audit_adapter.RalphAuditSinkAdapter(cap=8)

    total = 13
    for i in range(total):
        adapter.emit(_make_record(session_id="sess", tool_name=f"tool_{i}", run_id=f"run_{i}"))

    drained = adapter.drain_records()
    assert len(drained) == 8, (
        f"expected FIFO cap=8 to drop {total - 8} oldest records, "
        f"got {len(drained)} — unbounded buffer regression"
    )

    expected_run_ids = [f"run_{i}" for i in range(5, 13)]
    actual_run_ids = [record.correlation.run_id for record in drained]
    assert actual_run_ids == expected_run_ids, (
        f"FIFO order mismatch: expected {expected_run_ids}, got {actual_run_ids}"
    )


def test_flush_drains_buffer() -> None:
    adapter = audit_adapter.RalphAuditSinkAdapter(cap=8)

    for i in range(3):
        adapter.emit(_make_record(session_id="sess", tool_name=f"tool_{i}", run_id=f"run_{i}"))

    flush_result = adapter.flush()
    assert flush_result is None, (
        f"flush() must return None per the AuditSink Protocol "
        f"(def flush(self) -> None), got {type(flush_result).__name__}: {flush_result!r}"
    )

    drained_after_flush = adapter.drain_records()
    assert drained_after_flush == [], (
        f"flush() must clear the buffer; drain_records() after flush() "
        f"returned {len(drained_after_flush)} record(s) — flush is still a no-op"
    )


def test_default_constructor_preserves_unbounded_behavior() -> None:
    adapter = audit_adapter.RalphAuditSinkAdapter()
    adapter.emit(_make_record(session_id="sess", tool_name="t", run_id="r"))
    drained = adapter.drain_records()
    assert len(drained) == 1
