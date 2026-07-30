"""Benchmark data types extracted from ``bench``.

Holds ``ScriptedCall``, ``BenchmarkFixture``, ``BenchmarkCounters``,
and ``BenchmarkResult`` so :mod:`ralph.mcp.explore.bench` and the
per-fixture :mod:`ralph.mcp.explore._bench_fixtures` sub-module can
both depend on the type definitions without a circular import.

This module was extracted after the prior PEP 562 late-import
workaround (``# noqa: E402,F401`` markers on the bottom-of-file
``from ralph.mcp.explore._bench_fixtures import ...`` statement)
tripped the ``audit_lint_bypass`` invariant. The split keeps the
hub module dependency graph acyclic and the markers disappear.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class ScriptedCall:
    """A single scripted tool call used by a benchmark fixture."""

    tool: str
    params: Mapping[str, object]
    expected_evidence_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class BenchmarkFixture:
    """A single benchmark fixture (one question, two flows).

    AC-12: the fixture carries its own bounded transcript
    counters (``catalog_tokens`` and ``final_evidence_tokens``)
    so the harness can derive the full scripted-transcript
    total without the caller passing defaults of zero. The
    bench harness derives catalog tokens from the visible tool
    descriptions/input schemas the harness itself enumerates;
    callers do not need to inject them.
    """

    question_id: str
    description: str
    workspace_files: Mapping[str, str]
    baseline_script: tuple[ScriptedCall, ...]
    indexed_script: tuple[ScriptedCall, ...]
    expected_evidence_ids: tuple[str, ...]
    max_returned_bytes: int
    max_tool_calls: int
    requires_reindex: bool = False
    # AC-12: fixture-owned transcript token budget. The
    # ``catalog_tokens`` field is the bounded token cost of the
    # visible changed-tool descriptions plus input schemas; the
    # harness derives it from the visible catalog and overrides
    # this default with the derived value at run time. The
    # ``final_evidence_tokens`` field is the compact evidence
    # context the agent keeps after the flow ends. Both fields
    # default to ``0`` so callers without a measured catalog
    # remain back-compat.
    catalog_tokens: int = 0
    final_evidence_tokens: int = 0


@dataclass(frozen=True, slots=True)
class BenchmarkCounters:
    """Single-script counters."""

    tool_calls: int
    returned_bytes: int
    transcript_tokens: int
    wall_time_seconds: float
    stale_fallback_events: int
    evidence_recall: float
    evidence_precision: float


@dataclass(frozen=True, slots=True)
class BenchmarkResult:
    """Result of a single benchmark question (one fixture)."""

    question_id: str
    baseline: BenchmarkCounters
    indexed: BenchmarkCounters
    parse_count: int = 0
    changed_file_count: int = 0
    index_storage_bytes: int = 0
    notes: tuple[str, ...] = field(default_factory=tuple)

    def bytes_savings_ratio(self) -> float:
        """Return 1 - (indexed.bytes / baseline.bytes); 0.0 if baseline is 0."""
        if self.baseline.returned_bytes == 0:
            return 0.0
        return 1.0 - (self.indexed.returned_bytes / self.baseline.returned_bytes)

    def calls_within_budget(self, baseline_calls: int | None = None) -> bool:
        baseline = baseline_calls or self.baseline.tool_calls
        return self.indexed.tool_calls <= baseline


__all__ = [
    "BenchmarkCounters",
    "BenchmarkFixture",
    "BenchmarkResult",
    "ScriptedCall",
]
