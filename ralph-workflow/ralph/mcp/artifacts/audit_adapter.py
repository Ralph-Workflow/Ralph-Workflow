"""Audit adapter utilities for MCP records."""

from __future__ import annotations

import collections
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


# Default cap for ``RalphAuditSinkAdapter.__init__``. 4096 is generous
# for any realistic single-session audit volume; it matches the FIFO
# ring-buffer pattern used by ``BoundedLinesQueue`` /
# ``execution_history`` and the cap-policy documented in
# ``ralph-workflow/docs/agents/memory-lifecycle.md``. Defined at
# module load order BEFORE ``RalphAuditSinkAdapter`` so the default
# argument reference is resolved at class-definition time.
_DEFAULT_AUDIT_RECORD_CAP: int = 4096


class RalphAuditSinkAdapter:
    """Adapter that buffers Ralph audit records produced by MCP.

    wt-024 memory-perf AC-02: the ``_records`` buffer is bounded by a
    constructor-injected ``cap`` (default ``_DEFAULT_AUDIT_RECORD_CAP =
    4096``) using ``collections.deque(maxlen=cap)``. ``deque.append``
    honors ``maxlen`` by evicting the OLDEST record FIFO — the same
    pattern already used by ``BoundedLinesQueue``,
    ``execution_history``, and other production ring buffers in this
    codebase. The cap is exposed as a constructor parameter for DI /
    testability; existing no-arg callers
    (``tests/test_audit_adapter.py:49,69`` and any production wiring)
    keep working unchanged because ``cap`` defaults to the production
    cap.

    ``flush()`` is now Protocol-correct: it returns ``None`` (per the
    ``AuditSink`` Protocol ``def flush(self) -> None: ...``) AND clears
    the buffer so the buffered memory is released. The previous
    "documented no-op" was a latent leak enabler — buffered records
    could be retained until a ``drain_records()`` call without any
    production caller calling ``drain_records()`` periodically.
    """

    def __init__(self, cap: int = _DEFAULT_AUDIT_RECORD_CAP) -> None:
        if not isinstance(cap, int) or cap <= 0:
            raise ValueError(f"cap must be a positive int, got {cap!r}")
        self._records: collections.deque[RalphAuditRecord] = collections.deque(maxlen=cap)
        self._lock = Lock()

    def emit(self, record: McpAuditRecord) -> None:
        """Store a converted audit record in the buffer (FIFO-evicting)."""

        with self._lock:
            self._records.append(to_ralph_record(record))

    def drain_records(self) -> list[RalphAuditRecord]:
        """Return buffered records and clear the buffer."""

        with self._lock:
            drained = list(self._records)
            self._records.clear()
        return drained

    def flush(self) -> None:
        """Release buffered records (returns ``None`` per the ``AuditSink`` Protocol).

        Clears the FIFO buffer so buffered memory is released without
        requiring a caller to first ``drain_records()``. Returns
        ``None`` (does NOT return records — that would violate the
        Protocol's ``def flush(self) -> None`` signature).
        """
        with self._lock:
            self._records.clear()


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
