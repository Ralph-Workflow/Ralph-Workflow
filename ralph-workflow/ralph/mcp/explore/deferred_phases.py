"""Tracked deferral register for Phases 2-5 of the indexed exploration plan.

Each entry records the phase, its deliverables, a non-empty rationale
(gated on Phase 1 measurement passing / Phase 2 structure existing /
benchmark justification), the risk of staying deferred, and the
baseline_counters snapshot. The latter is filled by Phase 1 benchmark
harness (see :mod:`ralph.mcp.explore.bench`).

This is a pure data module; it has no I/O and is fully black-box
testable. Tests assert every deferred phase has a non-empty rationale
and a baseline_counters reference.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final

from ralph.mcp.explore.audit_register import AUDIT_REGISTER


@dataclass(frozen=True, slots=True)
class DeferredPhase:
    """A single deferred phase tracked under the audit register."""

    phase_id: str
    title: str
    deliverables: tuple[str, ...]
    deferral_rationale: str
    risk: str
    baseline_counters: dict[str, int | float | None] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.phase_id.strip():
            raise ValueError(f"DeferredPhase({self.phase_id!r}): phase_id must be non-empty")
        if not self.title.strip():
            raise ValueError(
                f"DeferredPhase({self.phase_id!r}): title must be non-empty"
            )
        if not self.deliverables:
            raise ValueError(
                f"DeferredPhase({self.phase_id!r}): deliverables must be non-empty"
            )
        if not self.deferral_rationale.strip():
            raise ValueError(
                f"DeferredPhase({self.phase_id!r}): deferral_rationale must be non-empty"
            )
        if not self.risk.strip():
            raise ValueError(
                f"DeferredPhase({self.phase_id!r}): risk must be non-empty"
            )


def _audit_baselines() -> dict[str, int | float | None]:
    """Snapshot the Phase 0 audit register into a baseline-counts dict."""
    snapshot: dict[str, int | float | None] = {}
    for entry in AUDIT_REGISTER:
        snapshot[entry.tool] = entry.counters.transcript_tokens if entry.counters else None
    return snapshot


_DEFERRED: tuple[DeferredPhase, ...] = (
    DeferredPhase(
        phase_id="phase_2",
        title="Python and Markdown structure (symbols + edges + ralph_graph neighbors)",
        deliverables=(
            "Python symbol extraction with stdlib ast",
            "Markdown heading/link extraction",
            "Basic edges: contains, defines, imports, tests, mentions",
            "Indexed optional arguments on list_directory/directory_tree",
            "ralph_graph with neighbors mode",
            "Symbol-aware and graph-aware ranking for search_files/grep_files",
        ),
        deferral_rationale=(
            "Gated on Phase 1 measurement passing the >=30% byte budget "
            "and 1.0 evidence recall. Phase 2 introduces semantic edges "
            "that Phase 1 explicitly cannot claim; shipping both at once "
            "would skip the prompt's ACI gate."
        ),
        risk=(
            "Without symbols, ranking cannot claim semantic importance; "
            "the lexical-only ranking is honest but coarse. Once Phase 1 "
            "proves the substrate works, Phase 2 lifts that ceiling."
        ),
        baseline_counters=_audit_baselines(),
    ),
    DeferredPhase(
        phase_id="phase_3",
        title="Impact-aware editing (ralph_graph impact/tests + edit_file targeting)",
        deliverables=(
            "ralph_graph with impact and tests modes",
            "edit_file arguments for expected_content_hash, target, "
            "match_strategy, impact_preview, reindex",
            "Conservative caller/importer/test suggestions",
            "Integration with git_status/git_diff ranking signals",
        ),
        deferral_rationale=(
            "Impact mode depends on Phase 2 symbol/edge extraction; it "
            "is meaningless without the structural graph. Tests mode is "
            "the same. The current Phase 1 lexical evidence-id flows "
            "already cover the read/search/grep efficiency gates."
        ),
        risk=(
            "Agents will continue to over-edit on broad search; an "
            "explicit 'inferred' marker on every impact result is the "
            "Phase 3 contract to avoid silent certainty."
        ),
        baseline_counters=_audit_baselines(),
    ),
    DeferredPhase(
        phase_id="phase_4",
        title="Non-index MCP remediation (artifact, planning, coordination, web, media; git_log/git_show)",
        deliverables=(
            "Compact artifact validation errors with repair pointers "
            "(ralph_submit_artifact, planning tools)",
            "Media/web bounded metadata and replayable handles "
            "(read_media, read_image, web_search, visit_url, download_url)",
            "Coordination tools with structured progress fields "
            "(report_progress, declare_complete, coordinate)",
            "git_log compact log cards and changed-path ranking",
            "git_show compact per-object lookup hints",
            "Planning tool compactness wins (submit_plan_section, "
            "finalize_plan, validate_draft, etc.)",
        ),
        deferral_rationale=(
            "The shipped non-index remediation for Phase 1 already covers "
            "git_status format=compact, git_diff format=summary, and "
            "exec format=summary (recorded as ADD_ARGUMENT outcomes in "
            "the audit register). unsafe_exec and raw_exec remain "
            "unchanged (KEEP) because the summary mode is intentionally "
            "only on the bounded exec path; the alias keeps the same "
            "capability surface. Phase 4 defers the remaining non-index "
            "families: git_log, git_show, artifact, planning, "
            "coordination, web, and media. The audit register records "
            "the SHIPPED outcomes for git_status, git_diff, exec and the "
            "KEEP outcomes for unsafe_exec and raw_exec; the deferred "
            "register is the single source of truth for the rest."
        ),
        risk=(
            "Agents may continue to over-fetch artifact/planning/web/ "
            "media output until compactness wins ship; the bounded-timeout "
            "contract already limits the worst case, and the audit "
            "register's DEFER rationale names the missing re-measurement "
            "evidence that gates each deferred outcome."
        ),
        baseline_counters=_audit_baselines(),
    ),
    DeferredPhase(
        phase_id="phase_5",
        title="Optional adapters (NetworkX / Kuzu / Tree-sitter)",
        deliverables=(
            "NetworkX offline graph metrics behind a feature flag",
            "Kuzu adapter gated by measured SQLite bottleneck evidence",
            "FTS + graph + git-change hybrid ranking with explicit scores",
            "Tree-sitter language parsers after Python/Markdown proves useful",
        ),
        deferral_rationale=(
            "Phase 5 is optional per the prompt. Kuzu's README says the "
            "project is being archived/moved, so it cannot be a phase-1 "
            "dependency. NetworkX is offline metrics only. Tree-sitter "
            "is multi-language coverage that Phase 2 (Python+Markdown) "
            "must prove useful first."
        ),
        risk=(
            "Limited to measured SQLite bottlenecks; if Phase 1 measurement "
            "reveals graph-walk performance problems, Phase 5 is the path "
            "to fix them."
        ),
        baseline_counters=_audit_baselines(),
    ),
)


DEFERRED_PHASES: Final[tuple[DeferredPhase, ...]] = _DEFERRED
"""Immutable Phase 2-5 deferral register, snapshot at module import."""


class DeferredPhaseRegistry:
    """Public read-only view of the deferred phase register.

    Wrapped as a class to leave room for late-bound baseline updates
    without changing the call sites.
    """

    @staticmethod
    def phases() -> tuple[DeferredPhase, ...]:
        return DEFERRED_PHASES

    @staticmethod
    def get(phase_id: str) -> DeferredPhase | None:
        for entry in DEFERRED_PHASES:
            if entry.phase_id == phase_id:
                return entry
        return None
