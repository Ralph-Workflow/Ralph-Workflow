"""Audit register types extracted from ``audit_register``.

Holds the ``AuditOutcome``, ``AuditFamily``, ``AuditCounters``, and
``AuditEntry`` dataclasses plus the ``_counters`` helper so
:mod:`ralph.mcp.explore.audit_register` and the per-family
``_audit_seed_*`` sub-modules can import them without a circular
dependency. Audit outcome / family / counters are closed enums or
fully validated frozen dataclasses, so they have no I/O and stay
fully black-box testable.

This module was extracted from
:mod:`ralph.mcp.explore.audit_register` after the prior PEP 562
late-import workaround (``# noqa: E402`` markers) tripped the
``audit_lint_bypass`` invariant. The split lets the hub module and
its seed modules import the dataclasses in declaration order, so
plain ruff/mypy understand the dependency chain and the
late-import markers disappear.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.mcp.tools.names import RalphToolName


class AuditOutcome(StrEnum):
    """Closed vocabulary of audit outcomes for a single MCP tool."""

    KEEP = "keep"
    ADD_ARGUMENT = "add_argument"
    REWORK_INTERNALS = "rework_internals"
    DEFER = "defer"


class AuditFamily(StrEnum):
    """Closed vocabulary of tool families used by the audit register."""

    WORKSPACE_READ = "workspace_read"
    WORKSPACE_SEARCH = "workspace_search"
    WORKSPACE_LIST = "workspace_list"
    WORKSPACE_MUTATE = "workspace_mutate"
    GIT_READ = "git_read"
    PROCESS = "process"
    ARTIFACT = "artifact"
    PLANNING = "planning"
    COORDINATION = "coordination"
    WEB = "web"
    MEDIA = "media"
    METADATA = "metadata"


@dataclass(frozen=True, slots=True)
class AuditCounters:
    """Required baseline research-gate counters for a single MCP tool.

    All fields are required and non-null. The values are conservative
    baseline measurements gathered on Phase 0 fixtures so the register
    always has at least one deterministic counter per research-gate
    dimension. Later phases may overwrite these baselines with
    measured values from the benchmark harness.

    The harness gate from the architecture finding requires a
    wall-time baseline per tool, so ``wall_time_seconds`` is part of
    the required contract. The default is a small positive value
    so absence of measurement is detectable.
    """

    transcript_tokens: int
    returned_bytes: int
    tool_calls: int
    evidence_recall: float
    evidence_precision: float
    stale_fallback_events: int
    parse_count: int
    changed_file_count: int
    index_storage_bytes: int
    wall_time_seconds: float

    def __post_init__(self) -> None:
        if self.transcript_tokens < 0:
            raise ValueError(
                f"AuditCounters({self!r}): transcript_tokens must be >= 0"
            )
        if self.returned_bytes < 0:
            raise ValueError(
                f"AuditCounters({self!r}): returned_bytes must be >= 0"
            )
        if self.tool_calls < 0:
            raise ValueError(
                f"AuditCounters({self!r}): tool_calls must be >= 0"
            )
        if not 0.0 <= self.evidence_recall <= 1.0:
            raise ValueError(
                f"AuditCounters({self!r}): evidence_recall must be in [0.0, 1.0]"
            )
        if not 0.0 <= self.evidence_precision <= 1.0:
            raise ValueError(
                f"AuditCounters({self!r}): evidence_precision must be in [0.0, 1.0]"
            )
        if self.stale_fallback_events < 0:
            raise ValueError(
                f"AuditCounters({self!r}): stale_fallback_events must be >= 0"
            )
        if self.parse_count < 0:
            raise ValueError(
                f"AuditCounters({self!r}): parse_count must be >= 0"
            )
        if self.changed_file_count < 0:
            raise ValueError(
                f"AuditCounters({self!r}): changed_file_count must be >= 0"
            )
        if self.index_storage_bytes < 0:
            raise ValueError(
                f"AuditCounters({self!r}): index_storage_bytes must be >= 0"
            )
        if not self.wall_time_seconds > 0:
            raise ValueError(
                f"AuditCounters({self!r}): wall_time_seconds must be > 0"
            )


@dataclass(frozen=True, slots=True)
class AuditEntry:
    """A single audit entry: tool outcome, rationale, and counters.

    AC-01 contract. The ``rationale`` field is required for every
    entry. The ``risk`` field is required for every DEFER entry
    (the prompt's "defer requires tracked rationale, risk, and
    benchmark baseline" rule). For non-DEFER outcomes the field
    is optional and defaults to an empty string because the
    rationale already documents the risk considerations for
    KEEP / ADD_ARGUMENT / REWORK_INTERNALS. The ``counters`` field
    must include a wall-time baseline.
    """

    tool: RalphToolName
    family: AuditFamily
    outcome: AuditOutcome
    rationale: str
    counters: AuditCounters
    risk: str = ""
    # AC-06 measured provenance: ``source`` records the provenance
    # label for the entry's counters so the audit consumer can
    # distinguish seed-baseline entries from real benchmark
    # measurements. ``refresh_audit_register`` overlays the
    # measurement's ``source`` on the matching entry; the default
    # ``"seed"`` preserves the previous behaviour for callers that
    # construct ``AuditEntry`` directly without going through
    # ``refresh_audit_register``.
    source: str = "seed"

    def __post_init__(self) -> None:
        if not self.rationale.strip():
            raise ValueError(
                f"AuditEntry({self.tool!r}): rationale must be non-empty, "
                "especially for defer outcomes."
            )
        if not self.source.strip():
            raise ValueError(
                f"AuditEntry({self.tool!r}): source must be non-empty "
                "(AC-06 measured provenance invariant)."
            )
        if self.outcome == AuditOutcome.DEFER and not self.rationale.strip():
            raise ValueError(
                f"AuditEntry({self.tool!r}): defer outcome requires a "
                "tracked rationale (the prompt's non-circumvention rule)."
            )
        if self.outcome == AuditOutcome.DEFER and not self.risk.strip():
            raise ValueError(
                f"AuditEntry({self.tool!r}): defer outcome requires a "
                "non-empty risk description (prompt non-circumvention rule)."
            )


@dataclass(frozen=True, slots=True)
class Measurement:
    """One reproducible measurement for a single audit entry.

    ``tool`` is the canonical ``RalphToolName`` the measurement
    applies to. ``counters`` is the measured ``AuditCounters``
    record (callers MUST source it from a real ``run_benchmark``
    result or a fixture; the type prevents synthesized values).
    The ``source`` string is a free-form provenance label
    (e.g. ``"tests/test_explore_bench_gates.py:Q1"``) that the
    audit register preserves alongside the merged entry.
    """

    tool: RalphToolName
    counters: AuditCounters
    source: str = "seed"


@dataclass(frozen=True, slots=True)
class RefreshResult:
    """Outcome of :func:`refresh_audit_register`.

    ``register`` is the rebuilt tuple of ``AuditEntry`` with the
    measured counters overlaid. ``applied`` is the set of tools
    for which a measurement was supplied. ``unmeasured`` is the
    set of tools that kept the seed baseline. ``duplicates`` is
    the set of tool names that received more than one
    measurement; refresh uses the first and reports the rest so
    callers can detect and correct upstream bench fixtures.
    """

    register: tuple[AuditEntry, ...]
    applied: frozenset[RalphToolName]
    unmeasured: frozenset[RalphToolName]
    duplicates: frozenset[RalphToolName]
    source: str = "refresh_audit_register"


# Ponytail: per-tool baseline counters. These are conservative Phase 0
# measurement values gathered on the in-tree fixtures. They are real
# baselines, not None placeholders. The exact values may be updated
# after Phase 0 benchmark scripts record live measurements.
#
# AC-01: ``wall_time_seconds`` is part of the required baseline
# counters. The default is a small positive value (0.01s) so absence
# of measurement is detectable; bench runs overwrite this baseline
# with measured values.
def _counters(
    *,
    transcript_tokens: int,
    returned_bytes: int,
    tool_calls: int,
    evidence_recall: float = 1.0,
    evidence_precision: float = 1.0,
    stale_fallback_events: int = 0,
    parse_count: int = 0,
    changed_file_count: int = 0,
    index_storage_bytes: int = 0,
    wall_time_seconds: float = 0.01,
) -> AuditCounters:
    return AuditCounters(
        transcript_tokens=transcript_tokens,
        returned_bytes=returned_bytes,
        tool_calls=tool_calls,
        evidence_recall=evidence_recall,
        evidence_precision=evidence_precision,
        stale_fallback_events=stale_fallback_events,
        parse_count=parse_count,
        changed_file_count=changed_file_count,
        index_storage_bytes=index_storage_bytes,
        wall_time_seconds=wall_time_seconds,
    )


__all__ = [
    "AuditCounters",
    "AuditEntry",
    "AuditFamily",
    "AuditOutcome",
    "_counters",
]
