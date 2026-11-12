from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class ScriptedCall:
    tool: str
    params: Mapping[str, object]
    expected_evidence_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class BenchmarkFixture:
    question_id: str
    description: str
    workspace_files: Mapping[str, str]
    baseline_script: tuple[ScriptedCall, ...]
    indexed_script: tuple[ScriptedCall, ...]
    expected_evidence_ids: tuple[str, ...]
    max_returned_bytes: int
    max_tool_calls: int
    requires_reindex: bool = False
    catalog_tokens: int = 0
    final_evidence_tokens: int = 0


@dataclass(frozen=True, slots=True)
class BenchmarkCounters:
    tool_calls: int
    returned_bytes: int
    transcript_tokens: int
    wall_time_seconds: float
    stale_fallback_events: int
    evidence_recall: float
    evidence_precision: float


@dataclass(frozen=True, slots=True)
class BenchmarkResult:
    question_id: str
    baseline: BenchmarkCounters
    indexed: BenchmarkCounters
    parse_count: int = 0
    changed_file_count: int = 0
    index_storage_bytes: int = 0
    notes: tuple[str, ...] = field(default_factory=tuple)
    response_times_seconds: tuple[float, ...] = ()
    retained_growth: tuple[tuple[str, int, int], ...] = ()

    def bytes_savings_ratio(self) -> float:
        if self.baseline.returned_bytes == 0:
            return 0.0
        return 1.0 - (self.indexed.returned_bytes / self.baseline.returned_bytes)

    def calls_within_budget(self, baseline_calls: int | None = None) -> bool:
        baseline = baseline_calls or self.baseline.tool_calls
        return self.indexed.tool_calls <= baseline


@runtime_checkable
class ToolResultLike(Protocol):
    content: Sequence[object]
    is_error: bool


@dataclass(frozen=True, slots=True)
class FlowTiming:
    flow_id: str
    samples_seconds: tuple[float, ...]
    p95_seconds: float


__all__ = [
    "BenchmarkCounters",
    "BenchmarkFixture",
    "BenchmarkResult",
    "FlowTiming",
    "ScriptedCall",
    "ToolResultLike",
]
