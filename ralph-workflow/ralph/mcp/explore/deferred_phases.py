"""Tracked deferral register for Phase 5 of the indexed exploration plan.

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
        phase_id="phase_5",
        title="Optional adapters (NetworkX / Kuzu / Tree-sitter)",
        deliverables=(
            "NetworkX offline graph metrics behind a feature flag",
            "Kuzu adapter gated by measured SQLite bottleneck evidence",
            "FTS + graph + git-change hybrid ranking with explicit scores",
            "Tree-sitter language parsers after Python/Markdown proves useful",
        ),
        deferral_rationale=(
            "Phase 5 is optional per the prompt. Phases 0-4 are fully "
            "shipped (Python + Markdown structure, ``ralph_graph``, "
            "impact-aware editing, and Phase-4 non-index remediation), "
            "so the only remaining deferrals are the optional adapters "
            "that are gated on measured SQLite bottleneck evidence. "
            "Kuzu's README says the project is being archived/moved, so "
            "it cannot be a phase-1 dependency. NetworkX is offline "
            "metrics only. Tree-sitter is multi-language coverage that "
            "the shipped Python+Markdown extractors must prove useful "
            "first."
        ),
        risk=(
            "Limited to measured SQLite bottlenecks; if Phase 1 "
            "measurement reveals graph-walk performance problems, "
            "Phase 5 is the path to fix them. If a future audit "
            "discovers a regression in the shipped Phases 0-4 "
            "implementation, the rationale and deliverables must "
            "be re-audited; the audit_register invariant "
            "test_audit_register_tracks_defer_outcomes pins the "
            "empty-defer contract so a drift is caught immediately."
        ),
        baseline_counters=_audit_baselines(),
    ),
)


DEFERRED_PHASES: Final[tuple[DeferredPhase, ...]] = _DEFERRED
"""Immutable Phase 5 deferral register, snapshot at module import."""


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
