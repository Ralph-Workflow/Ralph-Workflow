
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from typing import TYPE_CHECKING, Final

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

if TYPE_CHECKING:
    from ralph.mcp.explore._bench_types import BenchmarkResult

_FIXTURE_PRIMARY_TOOL: Final[dict[str, RalphToolName]] = {
    "Q1": RalphToolName.GREP_FILES,
    "Q2": RalphToolName.SEARCH_FILES,
    "Q3": RalphToolName.GREP_FILES,
    "Q4": RalphToolName.RALPH_GRAPH,
    "Q5": RalphToolName.EDIT_FILE,
    "Q6": RalphToolName.EDIT_FILE,
    "Q7": RalphToolName.GIT_STATUS,
    "Q8": RalphToolName.GIT_DIFF,
    "Q9": RalphToolName.EXEC,
    "Q10": RalphToolName.GIT_LOG,
    "Q11": RalphToolName.GIT_SHOW,
    "Q12": RalphToolName.WEB_SEARCH,
    "Q13": RalphToolName.READ_MEDIA,
}

_SEED: tuple[AuditEntry, ...] = (
    *_SEED_WORKSPACE,
    *_SEED_GIT_PROCESS,
    *_SEED_ARTIFACT_PLANNING,
    *_SEED_COORD_WEB_MEDIA,
)


AUDIT_REGISTER: Final[tuple[AuditEntry, ...]] = _SEED


def audit_register() -> tuple[AuditEntry, ...]:
    return AUDIT_REGISTER


def refresh_audit_register(
    measurements: Sequence[Measurement] | None = None,
    *,
    source: str = "refresh_audit_register",
) -> RefreshResult:
    grouped: dict[RalphToolName, list[Measurement]] = {}
    if measurements:
        for measurement in measurements:
            grouped.setdefault(measurement.tool, []).append(measurement)
    selected = {
        tool: _aggregate_measurements(tool_measurements)
        for tool, tool_measurements in grouped.items()
    }
    duplicates = {tool for tool, tool_measurements in grouped.items() if len(tool_measurements) > 1}
    rebuilt: list[AuditEntry] = []
    applied: set[RalphToolName] = set()
    for entry in AUDIT_REGISTER:
        if entry.tool in selected:
            measurement = selected[entry.tool]
            applied.add(entry.tool)
            rebuilt.append(
                replace(
                    entry,
                    counters=measurement.counters,
                    source=measurement.source,
                )
            )
        else:
            rebuilt.append(entry)
    unmeasured = frozenset(member for member in RalphToolName if member not in applied)
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
    "bench_results_to_measurements",
    "refresh_audit_register",
]


def _aggregate_measurements(measurements: Sequence[Measurement]) -> Measurement:

    if not measurements:
        raise ValueError("cannot aggregate an empty measurement sequence")
    first = measurements[0]
    counters = first.counters
    for measurement in measurements[1:]:
        other = measurement.counters
        counters = AuditCounters(
            transcript_tokens=counters.transcript_tokens + other.transcript_tokens,
            returned_bytes=counters.returned_bytes + other.returned_bytes,
            tool_calls=counters.tool_calls + other.tool_calls,
            evidence_recall=min(counters.evidence_recall, other.evidence_recall),
            evidence_precision=min(counters.evidence_precision, other.evidence_precision),
            stale_fallback_events=(counters.stale_fallback_events + other.stale_fallback_events),
            parse_count=counters.parse_count + other.parse_count,
            changed_file_count=(counters.changed_file_count + other.changed_file_count),
            index_storage_bytes=max(counters.index_storage_bytes, other.index_storage_bytes),
            wall_time_seconds=(counters.wall_time_seconds + other.wall_time_seconds),
        )
    return Measurement(
        tool=first.tool,
        counters=counters,
        source=" + ".join(measurement.source for measurement in measurements),
    )


def bench_results_to_measurements(
    results: Sequence[BenchmarkResult],
) -> list[Measurement]:
    measurements: list[Measurement] = []
    for result in results:
        question_id = result.question_id
        tool = _FIXTURE_PRIMARY_TOOL.get(question_id)
        if tool is None:
            continue
        indexed = result.indexed
        counters = AuditCounters(
            transcript_tokens=indexed.transcript_tokens,
            returned_bytes=indexed.returned_bytes,
            tool_calls=indexed.tool_calls,
            evidence_recall=indexed.evidence_recall,
            evidence_precision=indexed.evidence_precision,
            stale_fallback_events=indexed.stale_fallback_events,
            parse_count=result.parse_count,
            changed_file_count=result.changed_file_count,
            index_storage_bytes=result.index_storage_bytes,
            wall_time_seconds=indexed.wall_time_seconds,
        )
        measurements.append(
            Measurement(
                tool=tool,
                counters=counters,
                source=f"bench_fixtures/{question_id}",
            )
        )
    return measurements
