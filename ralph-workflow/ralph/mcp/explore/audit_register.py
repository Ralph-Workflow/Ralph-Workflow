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

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Final

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

    def __post_init__(self) -> None:
        if not self.rationale.strip():
            raise ValueError(
                f"AuditEntry({self.tool!r}): rationale must be non-empty, "
                "especially for defer outcomes."
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


# Phase 0 outcome seed from the architecture finding audit section.
# Keep entries must include a non-empty rationale. Defer entries
# MUST include a tracked rationale + a baseline reference (the
# prompt's "defer requires tracked rationale, risk, and benchmark
# baseline" rule).
#
# Ponytail: the per-family aggregates live in dedicated sub-modules
# so this hub stays under the per-file line ceiling while preserving
# the single ``AUDIT_REGISTER`` composition contract.
from ralph.mcp.explore._audit_seed_artifact_planning import (  # noqa: E402
    _SEED_ARTIFACT_PLANNING,
)
from ralph.mcp.explore._audit_seed_coord_web_media import (  # noqa: E402
    _SEED_COORD_WEB_MEDIA,
)
from ralph.mcp.explore._audit_seed_git_process import _SEED_GIT_PROCESS  # noqa: E402
from ralph.mcp.explore._audit_seed_workspace import _SEED_WORKSPACE  # noqa: E402

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
    "audit_register",
]
