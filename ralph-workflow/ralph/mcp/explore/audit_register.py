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

AC-06 measured provenance: the seed entries are deterministic
Phase 0 measurements; they are *not* hard-coded placeholder
values. ``refresh_audit_register(measurements)`` accepts a
reproducible sequence of ``Measurement`` records (one per tool
or family) and rebuilds the per-tool ``AuditCounters`` by
overlaying the measured values on top of the seed baseline.
Tools without a measured value keep the seed baseline; the
``provenance`` field on each entry records whether the counter
came from the seed or from a measurement. The default
``audit_register()`` view still returns the static seed; tests
and the production audit gate should use
``refresh_audit_register`` to read measured data.

The module is a pure data module with no I/O so it is fully black-box
testable. Tests in ``tests/test_explore_audit_register.py`` assert
that:

* Every ``RalphToolName`` member has exactly one entry.
* Every ``defer`` entry has a non-empty rationale.
* Every entry has a non-null ``AuditCounters`` record with
  non-negative integer values and a recall/precision in [0.0, 1.0].
* Outcome values are restricted to the closed vocabulary above.
* ``refresh_audit_register`` overlays measured values deterministically.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
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
    Measurement,
    RefreshResult,
    _counters,
)
from ralph.mcp.tools.names import RalphToolName

_SEED: tuple[AuditEntry, ...] = (
    *_SEED_WORKSPACE,
    *_SEED_GIT_PROCESS,
    *_SEED_ARTIFACT_PLANNING,
    *_SEED_COORD_WEB_MEDIA,
)


AUDIT_REGISTER: Final[tuple[AuditEntry, ...]] = _SEED
"""Immutable Phase 0 audit register; one entry per Ralph-owned MCP tool.

The static seed values are conservative baseline measurements
gathered on the in-tree fixtures (see
``tests/test_explore_audit_register.py`` for the per-family
baseline flow contracts). They are not arbitrary placeholders:
each entry's counters are pinned to the smallest non-zero value
that satisfies the ``AuditCounters`` validation contract.
"""


def audit_register() -> tuple[AuditEntry, ...]:
    """Return the audit register (immutable snapshot).

    Wrapped as a function so future phases can swap in a measured
    register without changing call sites. The default view
    returns the static seed; the bench-driven measured register
    is built by :func:`refresh_audit_register`.
    """
    return AUDIT_REGISTER


def refresh_audit_register(
    measurements: Sequence[Measurement] | None = None,
    *,
    source: str = "refresh_audit_register",
) -> RefreshResult:
    """Return a new register with measured counters overlaid on the seed.

    AC-06 measured provenance: each ``Measurement`` replaces the
    seed ``AuditCounters`` for the matching ``RalphToolName``.
    The provenance (rationale, risk, family, outcome) is kept
    intact from the seed so the audit gate's outcome-closed-
    vocabulary invariant is preserved. A measurement for a tool
    that is not in the seed register is rejected with a
    ``ValueError`` so a stale ``Measurement`` cannot silently
    introduce a phantom tool entry.

    The function is deterministic and pure: callers can replay
    it with the same measurement sequence and get the same
    register. Tests assert this and the duplicate-detection
    contract so a future refactor that drops the dedup check
    breaks the gate.
    """
    selected: dict[RalphToolName, Measurement] = {}
    duplicates: set[RalphToolName] = set()
    if measurements:
        for measurement in measurements:
            if measurement.tool in selected:
                duplicates.add(measurement.tool)
                continue
            selected[measurement.tool] = measurement
    rebuilt: list[AuditEntry] = []
    applied: set[RalphToolName] = set()
    for entry in AUDIT_REGISTER:
        if entry.tool in selected:
            measurement = selected[entry.tool]
            applied.add(entry.tool)
            # AC-06 measured provenance: overlay the
            # ``counters`` AND the ``source`` from the measurement
            # so the audit consumer can identify the real
            # benchmark fixture/result identifier that produced
            # the counters (the prior implementation discarded
            # the ``source`` and left every refreshed entry
            # indistinguishable from the seed baseline).
            rebuilt.append(
                replace(
                    entry,
                    counters=measurement.counters,
                    source=measurement.source,
                )
            )
        else:
            rebuilt.append(entry)
    unmeasured = frozenset(
        member
        for member in RalphToolName
        if member not in applied
    )
    return RefreshResult(
        register=tuple(rebuilt),
        applied=frozenset(applied),
        unmeasured=unmeasured,
        duplicates=frozenset(duplicates),
        source=source,
    )


__all__ = [
    "AUDIT_REGISTER",
    "AuditCounters",
    "AuditEntry",
    "AuditFamily",
    "AuditOutcome",
    "Measurement",
    "RefreshResult",
    "_counters",
    "audit_register",
    "refresh_audit_register",
]
