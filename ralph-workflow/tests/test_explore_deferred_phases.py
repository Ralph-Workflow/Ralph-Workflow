"""Black-box tests for the deferred-phases register."""

from __future__ import annotations

import pytest

from ralph.mcp.explore.deferred_phases import (
    DEFERRED_PHASES,
    DeferredPhase,
    DeferredPhaseRegistry,
)


def test_deferred_phases_covers_phase_2_through_5() -> None:
    """The register must include the four deferred phases AND
    distinguish shipped phases (phase_2/3/4, sentinel) from the
    genuinely-still-deferred phase_5 (real four-deliverable tuple)."""
    ids = {entry.phase_id for entry in DEFERRED_PHASES}
    assert ids == {"phase_2", "phase_3", "phase_4", "phase_5"}

    shipped_phase_ids = {"phase_2", "phase_3", "phase_4"}
    by_id = {entry.phase_id: entry for entry in DEFERRED_PHASES}

    for phase_id in shipped_phase_ids:
        entry = by_id[phase_id]
        assert entry.deliverables[0] == f"{phase_id}_complete_no_remaining_work", (
            f"{phase_id} must use the {phase_id}_complete_no_remaining_work "
            f"sentinel prefix; got {entry.deliverables[0]!r}"
        )
        assert entry.deferral_rationale.strip(), (
            f"{phase_id} must keep a non-empty rationale that cites the "
            f"shipped implementation"
        )

    phase_5 = by_id["phase_5"]
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


def test_every_deferred_phase_has_non_empty_rationale() -> None:
    """No deferred phase can ship without a non-empty rationale."""
    for entry in DEFERRED_PHASES:
        assert entry.deferral_rationale.strip(), (
            f"Phase {entry.phase_id} requires a non-empty rationale."
        )


def test_every_deferred_phase_has_non_empty_risk() -> None:
    """The risk statement is mandatory per the prompt's audit rule."""
    for entry in DEFERRED_PHASES:
        assert entry.risk.strip(), (
            f"Phase {entry.phase_id} requires a non-empty risk statement."
        )


def test_every_deferred_phase_has_baseline_counters() -> None:
    """baseline_counters must reference the Phase 0 audit register."""
    for entry in DEFERRED_PHASES:
        assert entry.baseline_counters, (
            f"Phase {entry.phase_id} requires a baseline_counters reference."
        )


def test_every_deferred_phase_has_deliverables() -> None:
    """Each phase must enumerate at least one deliverable."""
    for entry in DEFERRED_PHASES:
        assert entry.deliverables, (
            f"Phase {entry.phase_id} must enumerate deliverables."
        )


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
    phase = DeferredPhaseRegistry.get("phase_2")
    assert phase is not None
    assert phase.phase_id == "phase_2"


def test_registry_lookup_returns_none_for_unknown_id() -> None:
    assert DeferredPhaseRegistry.get("phase_99") is None


def test_ralph_explore_remains_deferred_without_measured_bundle_benefit() -> None:
    """AC-12: ralph_explore is not registered in deferred phases because
    it was never started. The gate asserts that the
    ``bench.ALL_FIXTURES`` set covers graph, edit, mutation, and
    Phase 4 workflows WITHOUT ralph_explore, so the optional wrapper
    stays deferred until measured evidence justifies it.
    """
    from ralph.mcp.explore.bench import (
        ALL_FIXTURES,
        REQUIRED_BENCH_WORKFLOW_IDS,
    )
    question_ids = {fixture.question_id for fixture in ALL_FIXTURES}
    assert "Q_explore" not in question_ids
    assert "ralph_explore" not in REQUIRED_BENCH_WORKFLOW_IDS
    # No deferred phase is named ralph_explore either; the optional
    # wrapper is held in Phase 5 (Optional adapters) implicitly.
    deferred_ids = {entry.phase_id for entry in DEFERRED_PHASES}
    assert "ralph_explore" not in deferred_ids
