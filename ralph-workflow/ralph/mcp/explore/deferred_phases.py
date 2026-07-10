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
            # Phase 2 has shipped; this deliverable list is intentionally a
            # single sentinel entry to mirror the Phase 4 shape -- there is
            # no remaining Phase 2 work to defer. The phase stays in the
            # register so the zero-DEFER audit-register contract continues
            # to be testable.
            "phase_2_complete_no_remaining_work",
        ),
        deferral_rationale=(
            "Phase 2 is no longer deferred: Python symbol extraction with "
            "stdlib ``ast`` lives in ``ralph/mcp/explore/structure.py``; "
            "Markdown heading + link extraction lives in "
            "``ralph/mcp/explore/structure.py:extract_markdown`` (called via "
            "``ralph/mcp/explore/structure.py:extract_structure``); the "
            "``contains`` / ``defines`` / ``imports`` / ``tests`` / "
            "``mentions`` edges are emitted by "
            "``ralph/mcp/explore/structure.py`` and persisted through "
            "``ralph/mcp/explore/pipeline.py:ExploreStore.replace_structure_rows``; "
            "``ralph_graph`` ``neighbors`` mode ships in "
            "``ralph/mcp/explore/graph.py:neighbors``; the ranked "
            "``contains_symbol`` / ``changed_only`` / "
            "``return_evidence_ids`` selectors on ``search_files`` and "
            "``grep_files`` route through "
            "``ralph/mcp/tools/workspace/_grep_handlers.py`` with the "
            "LexRanking score-component ladder in "
            "``ralph/mcp/explore/ranking.py``."
        ),
        risk=(
            "If a future audit finds a regression in Phase 2's structure "
            "extraction or graph-neighbor ordering, this entry must be "
            "re-audited and either moved back to an actionable deferral "
            "or have its sentinel retained with the regression recorded "
            "in this rationale. The "
            "test_deferred_phases_covers_phase_2_through_5 invariant "
            "pins the sentinel-prefix contract so a drift is caught "
            "immediately."
        ),
        baseline_counters=_audit_baselines(),
    ),
    DeferredPhase(
        phase_id="phase_3",
        title="Impact-aware editing (ralph_graph impact/tests + edit_file targeting)",
        deliverables=(
            # Phase 3 has shipped; this deliverable list is intentionally a
            # single sentinel entry to mirror the Phase 4 shape -- there is
            # no remaining Phase 3 work to defer. The phase stays in the
            # register so the zero-DEFER audit-register contract continues
            # to be testable.
            "phase_3_complete_no_remaining_work",
        ),
        deferral_rationale=(
            "Phase 3 is no longer deferred: ``ralph_graph`` ``impact`` "
            "mode lives in ``ralph/mcp/explore/graph.py:impact`` (~110 "
            "lines starting at line 551) and uses ``_IMPACT_RELATIONS`` "
            "(``ralph/mcp/explore/graph.py`` line 60) to bound relations "
            "per ``change_kind``; ``ralph_graph`` ``tests`` mode lives "
            "in ``ralph/mcp/explore/graph.py:tests_for`` (line 774) and "
            "returns ``suggested_tests`` with confidence + evidence_ids; "
            "``edit_file`` accepts ``expected_content_hash`` / ``target`` "
            "/ ``match_strategy`` / ``impact_preview`` / ``reindex`` / "
            "``return_evidence_updates`` per the MCP schema in "
            "``ralph/mcp/tools/bridge/_specs_file_write.py`` and the spec "
            "is enforced by ``handle_edit_file`` in "
            "``ralph/mcp/tools/workspace/_write_handlers.py`` (line 172); "
            "the conservative impact labels ``rename`` / ``signature`` / "
            "``behavior`` / ``delete`` / ``unknown`` are defined by "
            "``_IMPACT_RELATIONS`` in "
            "``ralph/mcp/explore/graph.py``."
        ),
        risk=(
            "If a future audit finds a regression in ``ralph_graph`` "
            "``impact`` / ``tests`` ordering or in the ``edit_file`` "
            "spec enforcement, this entry must be re-audited and either "
            "moved back to an actionable deferral or have its sentinel "
            "retained with the regression recorded in this rationale. "
            "The test_deferred_phases_covers_phase_2_through_5 invariant "
            "pins the sentinel-prefix contract so a drift is caught "
            "immediately."
        ),
        baseline_counters=_audit_baselines(),
    ),
    DeferredPhase(
        phase_id="phase_4",
        title="Non-index MCP remediation (artifact, planning, coordination, web, media; git_log/git_show)",
        deliverables=(
            # Phase 4 promotes every previously-DEFER non-index family
            # out of DEFER, so this deliverable list is intentionally
            # a single sentinel entry: there is no remaining Phase 4
            # work to defer. The phase stays in the register as a
            # sentinel so future audits can confirm that no audited
            # tool has been left in an unaudited state.
            "phase_4_complete_no_remaining_work",
        ),
        deferral_rationale=(
            "Phase 4 ships every previously-deferred non-index family. "
            "git_log / git_show / web_search / visit_url / download_url / "
            "read_image / read_media are promoted to ADD_ARGUMENT with a "
            "new ``format`` arg; the artifact submission tool, the "
            "eleven planning tools, and the three coordination tools are "
            "promoted to KEEP because the existing structured behavior "
            "(per-field ``code``+``repair`` ValidationError envelopes, "
            "bounded coordination payloads with structured marker "
            "suffixes) already matches the Phase-4 acceptance contract. "
            "git_status / git_diff / exec were shipped in Phase 1 as "
            "ADD_ARGUMENT and unsafe_exec / raw_exec remain KEEP "
            "because the summary mode is intentionally only on the "
            "bounded exec path. The audit register is now the single "
            "source of truth for the post-Phase-4 outcomes; no tool "
            "is left in an 'audit found inefficient but no decision' "
            "state."
        ),
        risk=(
            "If a future audit finds a regression in the structured "
            "behavior cited by a KEEP entry, the entry must be re-audited "
            "and either moved to REWORK_INTERNALS or DEFER with a "
            "non-empty rationale. The audit_register invariant "
            "test_audit_register_tracks_defer_outcomes pins the empty-"
            "defer contract so a drift is caught immediately."
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
