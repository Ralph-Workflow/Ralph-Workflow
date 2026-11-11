from __future__ import annotations

import pytest

from ralph.mcp.explore.deferred_phases import (
    DEFERRED_PHASES,
    DeferredPhase,
    DeferredPhaseRegistry,
)


def test_deferred_phases_retains_only_phase_5() -> None:
    ids = {entry.phase_id for entry in DEFERRED_PHASES}
    assert ids == {"phase_5"}, (
        "Only phase_5 should remain in DEFERRED_PHASES once phases 0-4 ship. Found: " + repr(ids)
    )

    phase_5 = next(entry for entry in DEFERRED_PHASES if entry.phase_id == "phase_5")
    assert phase_5.deliverables == (
        "NetworkX offline graph metrics behind a feature flag",
        "Kuzu adapter gated by measured SQLite bottleneck evidence",
        "FTS + graph + git-change hybrid ranking with explicit scores",
        "Tree-sitter language parsers after Python/Markdown proves useful",
    ), (
        "phase_5 must keep its original four-deliverable tuple because "
        "NetworkX/Kuzu/Tree-sitter are not yet implemented (no networkx, "
        "kuzu, or tree-sitter import in ralph/mcp/explore/ or pyproject.toml)"
    )
    assert phase_5.deferral_rationale.strip()
    assert phase_5.risk.strip()


def test_every_deferred_phase_has_non_empty_rationale() -> None:
    for entry in DEFERRED_PHASES:
        assert entry.deferral_rationale.strip(), (
            f"Phase {entry.phase_id} requires a non-empty rationale."
        )


def test_every_deferred_phase_has_non_empty_risk() -> None:
    for entry in DEFERRED_PHASES:
        assert entry.risk.strip(), f"Phase {entry.phase_id} requires a non-empty risk statement."


def test_every_deferred_phase_has_baseline_counters() -> None:
    for entry in DEFERRED_PHASES:
        assert entry.baseline_counters, (
            f"Phase {entry.phase_id} requires a baseline_counters reference."
        )


def test_every_deferred_phase_has_deliverables() -> None:
    for entry in DEFERRED_PHASES:
        assert entry.deliverables, f"Phase {entry.phase_id} must enumerate deliverables."


def test_deferred_phase_rejects_empty_rationale() -> None:
    with pytest.raises(ValueError, match="deferral_rationale must be non-empty"):
        DeferredPhase(
            phase_id="phase_x",
            title="Title",
            deliverables=("a",),
            deferral_rationale="",
            risk="risk",
        )


def test_deferred_phase_rejects_empty_deliverables() -> None:
    with pytest.raises(ValueError, match="deliverables must be non-empty"):
        DeferredPhase(
            phase_id="phase_x",
            title="Title",
            deliverables=(),
            deferral_rationale="rationale",
            risk="risk",
        )


def test_registry_lookup_returns_matching_phase() -> None:
    phase = DeferredPhaseRegistry.get("phase_5")
    assert phase is not None
    assert phase.phase_id == "phase_5"


def test_registry_lookup_returns_none_for_unknown_id() -> None:
    assert DeferredPhaseRegistry.get("phase_99") is None


def test_ralph_explore_remains_deferred_without_measured_bundle_benefit() -> None:
    from ralph.mcp.explore.bench import (
        ALL_FIXTURES,
        REQUIRED_BENCH_WORKFLOW_IDS,
    )

    question_ids = {fixture.question_id for fixture in ALL_FIXTURES}
    assert "Q_explore" not in question_ids
    assert "ralph_explore" not in REQUIRED_BENCH_WORKFLOW_IDS
    deferred_ids = {entry.phase_id for entry in DEFERRED_PHASES}
    assert "ralph_explore" not in deferred_ids
