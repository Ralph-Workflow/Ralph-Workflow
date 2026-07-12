"""Indexed exploration substrate for Ralph Workflow MCP tools.

This package owns the deterministic SQLite+FTS5 indexed exploration
substrate and the supporting typed registers (audit, deferred phases)
required by the MCP tool efficiency plan.

Phases 0-4 of the architecture finding are implemented here: the
Phase 0 MCP tool-efficiency audit register, Phase 1 lexical indexed
search/list/read plus ``ralph_index_status`` / ``ralph_reindex``,
Phase 2 Python + Markdown structure extraction and ``ralph_graph``
neighbors, Phase 3 impact-aware editing with ``ralph_graph``
impact/tests, and Phase 4 non-index MCP remediation. The sole
remaining entry in :mod:`ralph.mcp.explore.deferred_phases` is
``phase_5``: optional adapters (NetworkX offline metrics, an
optional Kuzu adapter, FTS+graph+git hybrid ranking, and
Tree-sitter language parsers), all gated on measured SQLite
bottleneck / benchmark evidence. The optional ``ralph_explore``
workflow wrapper is held implicitly under Phase 5 (it is not
registered as a separate deferred phase) until benchmarked flows
prove a compact task-oriented evidence packet is needed.

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
    "DEFERRED_PHASES",
    "AuditEntry",
    "AuditOutcome",
    "DeferredPhase",
    "DeferredPhaseRegistry",
    "audit_register",
]
