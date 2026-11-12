from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.mcp.tools.names import RalphToolName


class AuditOutcome(StrEnum):
    KEEP = "keep"
    ADD_ARGUMENT = "add_argument"
    REWORK_INTERNALS = "rework_internals"
    DEFER = "defer"


class AuditFamily(StrEnum):
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
            raise ValueError(f"AuditCounters({self!r}): transcript_tokens must be >= 0")
        if self.returned_bytes < 0:
            raise ValueError(f"AuditCounters({self!r}): returned_bytes must be >= 0")
        if self.tool_calls < 0:
            raise ValueError(f"AuditCounters({self!r}): tool_calls must be >= 0")
        if not 0.0 <= self.evidence_recall <= 1.0:
            raise ValueError(f"AuditCounters({self!r}): evidence_recall must be in [0.0, 1.0]")
        if not 0.0 <= self.evidence_precision <= 1.0:
            raise ValueError(f"AuditCounters({self!r}): evidence_precision must be in [0.0, 1.0]")
        if self.stale_fallback_events < 0:
            raise ValueError(f"AuditCounters({self!r}): stale_fallback_events must be >= 0")
        if self.parse_count < 0:
            raise ValueError(f"AuditCounters({self!r}): parse_count must be >= 0")
        if self.changed_file_count < 0:
            raise ValueError(f"AuditCounters({self!r}): changed_file_count must be >= 0")
        if self.index_storage_bytes < 0:
            raise ValueError(f"AuditCounters({self!r}): index_storage_bytes must be >= 0")
        if not self.wall_time_seconds > 0:
            raise ValueError(f"AuditCounters({self!r}): wall_time_seconds must be > 0")


@dataclass(frozen=True, slots=True)
class AuditEntry:
    tool: RalphToolName
    family: AuditFamily
    outcome: AuditOutcome
    rationale: str
    counters: AuditCounters
    risk: str = ""
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
    tool: RalphToolName
    counters: AuditCounters
    source: str = "seed"


@dataclass(frozen=True, slots=True)
class RefreshResult:
    register: tuple[AuditEntry, ...]
    applied: frozenset[RalphToolName]
    unmeasured: frozenset[RalphToolName]
    duplicates: frozenset[RalphToolName]
    source: str = "refresh_audit_register"


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
