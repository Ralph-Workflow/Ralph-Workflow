"""Indexed exploration substrate for Ralph Workflow MCP tools.

This package owns the deterministic SQLite+FTS5 indexed exploration
substrate and the supporting typed registers (audit, deferred phases)
required by the MCP tool efficiency plan.

Phase 0 + Phase 1 of the architecture finding are implemented here.
Phases 2-5 (symbol/graph extraction, impact-aware editing, non-index
MCP remediation, optional adapters) are recorded in
:mod:`ralph.mcp.explore.deferred_phases` and explicitly NOT implemented
in this slice.

The substrate is a disposable, deterministic cache under
``.agent/ralph-explore/index.sqlite``. Deleting that directory forces a
cold rebuild and never affects source files or workflow artifacts.
"""

from __future__ import annotations

from ralph.mcp.explore.audit_register import (
    AUDIT_REGISTER,
    AuditEntry,
    AuditOutcome,
    audit_register,
)
from ralph.mcp.explore.deferred_phases import (
    DEFERRED_PHASES,
    DeferredPhase,
    DeferredPhaseRegistry,
)

__all__ = [
    "AUDIT_REGISTER",
    "AuditEntry",
    "AuditOutcome",
    "DEFERRED_PHASES",
    "DeferredPhase",
    "DeferredPhaseRegistry",
    "audit_register",
]