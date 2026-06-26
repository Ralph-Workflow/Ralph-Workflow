"""Black-box memory contract tests for ``RalphAuditSinkAdapter``.

wt-024 memory-perf AC-02: the ``RalphAuditSinkAdapter``'s internal
``_records`` buffer MUST be bounded by a constructor-injected cap with
FIFO eviction (oldest records dropped when full), and ``flush()`` MUST
clear the buffer (the ``AuditSink`` Protocol types ``flush() -> None``,
so ``flush()`` returns ``None`` and CLEARS the buffer — it does NOT
return records; that would violate the Protocol).

These tests are BLACK-BOX — they only call ``emit()``,
``drain_records()``, and ``flush()``. They mirror the canonical pattern
at ``tests/test_audit_adapter.py`` (single-threaded, in-memory). The
adapter's internal ``_records`` field is never read by tests.

Fails today because:
  (1) ``RalphAuditSinkAdapter()`` has no ``cap`` parameter — the
      ``RalphAuditSinkAdapter(cap=8)`` constructor invocation raises
      ``TypeError: __init__() got an unexpected keyword argument 'cap'``
      before the test body runs;
  (2) the underlying buffer is an unbounded ``list`` so a 13-emit run
      returns all 13 records from ``drain_records()``;
  (3) ``flush()`` is a no-op, so calling it does NOT clear the buffer
      and a subsequent ``drain_records()`` still returns the records.
"""

from __future__ import annotations

from ralph.mcp.artifacts import audit_adapter
from ralph.mcp.protocol.capability_mapping import (
    AccessDecision,
    McpCapability,
    PolicyMode,
)


def _make_record(session_id: str, tool_name: str, run_id: str) -> audit_adapter.McpAuditRecord:
    """Build a single ``McpAuditRecord`` mirroring the canonical shape in
    ``tests/test_audit_adapter.py`` (timestamp_nanos, session_id,
    tool_name, decision, path, capability, metadata). Each call uses a
    unique ``run_id`` so the resulting ``RalphAuditRecord`` is
    distinguishable by the FIFO-ordering assertions below.
    """
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
    """Emitting past the constructor-injected cap MUST drop the oldest
    records FIFO.

    Drives 13 emits through ``RalphAuditSinkAdapter(cap=8)`` and
    asserts:
      - ``drain_records()`` returns EXACTLY 8 records (the cap);
      - the 8 returned records are the LAST 8 emitted (FIFO eviction
        drops the oldest 5, identifiable by the unique ``run_id``).
    """
    adapter = audit_adapter.RalphAuditSinkAdapter(cap=8)

    total = 13
    for i in range(total):
        adapter.emit(_make_record(session_id="sess", tool_name=f"tool_{i}", run_id=f"run_{i}"))

    drained = adapter.drain_records()
    assert len(drained) == 8, (
        f"expected FIFO cap=8 to drop {total - 8} oldest records, "
        f"got {len(drained)} — unbounded buffer regression"
    )

    # FIFO: the returned records are the LAST 8 emitted (indices 5..12).
    # Verify by walking each returned RalphAuditRecord.correlation.run_id.
    expected_run_ids = [f"run_{i}" for i in range(5, 13)]
    actual_run_ids = [record.correlation.run_id for record in drained]
    assert actual_run_ids == expected_run_ids, (
        f"FIFO order mismatch: expected {expected_run_ids}, got {actual_run_ids}"
    )


def test_flush_drains_buffer() -> None:
    """``flush()`` MUST return ``None`` and clear the buffer.

    The ``AuditSink`` Protocol types ``def flush(self) -> None: ...``,
    so ``flush()`` returns ``None`` — it does NOT return records
    (returning records would violate the Protocol). And it MUST
    actually clear the buffer so subsequent ``drain_records()`` is
    empty.

    Emits 3 records, calls ``flush()``, and asserts:
      - ``flush()`` returns ``None`` (Protocol-compliant);
      - a subsequent ``drain_records()`` returns ``[]`` (the buffer was
        cleared, proving ``flush()`` is NOT a documented no-op).
    """
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
    """The default constructor (no args) MUST stay backward-compatible:
    no-arg calls like ``RalphAuditSinkAdapter()`` from existing
    production wiring (tests/test_audit_adapter.py:49,69 and any
    future wiring) must keep working unchanged.

    Asserts:
      - default-cap accepts emits without TypeError;
      - at least the documented default cap (4096, set in step 5) is
        honored without raising.
    """
    adapter = audit_adapter.RalphAuditSinkAdapter()
    adapter.emit(_make_record(session_id="sess", tool_name="t", run_id="r"))
    drained = adapter.drain_records()
    assert len(drained) == 1
