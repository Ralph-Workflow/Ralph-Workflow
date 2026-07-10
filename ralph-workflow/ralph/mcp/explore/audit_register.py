"""Per-tool audit register for every Ralph-owned MCP tool.

This module owns a typed, immutable register with one entry per
``RalphToolName`` member. Each entry records the audit outcome
(``keep`` / ``add_argument`` / ``rework_internals`` / ``defer``), a
rationale, and required research-gate counters (transcript tokens,
returned bytes, tool calls, evidence recall, evidence precision,
stale/fallback events, parse count, changed file count, index
storage bytes).

Outcomes are seeded from the Phase 0 architecture finding audit
section. They can be updated by later phases after measurement
proves a deferral is unjustified or a ``keep`` requires rework.

The module is a pure data module with no I/O so it is fully black-box
testable. Tests in ``tests/test_explore_audit_register.py`` assert
that:

* Every ``RalphToolName`` member has exactly one entry.
* Every ``defer`` entry has a non-empty rationale.
* Every entry has a non-null ``AuditCounters`` record with
  non-negative integer values and a recall/precision in [0.0, 1.0].
* Outcome values are restricted to the closed vocabulary above.
"""

from __future__ import annotations

from typing import Final

from ralph.mcp.explore._audit_seed_artifact_planning import (
    _SEED_ARTIFACT_PLANNING,
)
from ralph.mcp.explore._audit_seed_coord_web_media import (
    _SEED_COORD_WEB_MEDIA,
)
from ralph.mcp.explore._audit_seed_git_process import _SEED_GIT_PROCESS
from ralph.mcp.explore._audit_seed_workspace import _SEED_WORKSPACE
from ralph.mcp.explore._audit_types import (
    AuditCounters,
    AuditEntry,
    AuditFamily,
    AuditOutcome,
    _counters,
)

_SEED: tuple[AuditEntry, ...] = (
    *_SEED_WORKSPACE,
    *_SEED_GIT_PROCESS,
    *_SEED_ARTIFACT_PLANNING,
    *_SEED_COORD_WEB_MEDIA,
)


AUDIT_REGISTER: Final[tuple[AuditEntry, ...]] = _SEED
"""Immutable Phase 0 audit register; one entry per Ralph-owned MCP tool."""


def audit_register() -> tuple[AuditEntry, ...]:
    """Return the audit register (immutable snapshot).

    Wrapped as a function so future phases can swap in a measured
    register without changing call sites.
    """
    return AUDIT_REGISTER


__all__ = [
    "AUDIT_REGISTER",
    "AuditCounters",
    "AuditEntry",
    "AuditFamily",
    "AuditOutcome",
    "_counters",
    "audit_register",
]
