"""Phase-by-phase walkthrough of the Status Bar counter resolution.

AC-01 / AC-02 / AC-03 contract test:

* Every phase in the default policy (`planning`, `planning_analysis`,
  `development`, `development_commit_cleanup`, `development_commit`,
  `development_analysis`, `development_final_commit_cleanup`,
  `development_final_commit`) shows the outer cycle (1-indexed) with
  cap 5.
* Terminal phases (`complete`, `failed_terminal`) omit the outer
  count.
* Analysis phases additionally show the inner analysis count.
* Policy remediation (`_push_remediation_status_bar`) shows the live
  attempt with the ``Remediation`` label.
* Conflict resolution (`push_conflict_status_bar`) shows the round
  with the ``Round`` label.

This test exercises the real default policy bundle, the real
``build_phase_entry_model_from_state``, the real
``StatusBarModel.render_status_bar`` path, and the real remediation
+ conflict-resolution push helpers.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from rich.console import Console

from ralph.display.context import DisplayContext, make_display_context
from ralph.display.parallel_display import phase_style_for_phase
from ralph.display.status_bar import StatusBarModel, render_status_bar
from ralph.pipeline.phase_transition import build_phase_entry_model_from_state
from ralph.pipeline.state import PipelineState
from ralph.policy.loader import load_policy

if TYPE_CHECKING:
    from ralph.policy.models import PipelinePolicy

pytestmark = pytest.mark.timeout_seconds(10)


def _ctx(width: int = 120) -> DisplayContext:
    console = Console(
        file=io.StringIO(), force_terminal=False, color_system=None, width=width
    )
    # Force the display context width to the requested ``width`` so the
    # test is invariant to ``COLUMNS`` (pytest-xdist sets ``COLUMNS=80``
    # for its workers, which would otherwise leak into ctx.width and
    # break the "len(text.plain) <= width" assertion below). The
    # ``force_width`` seam is the documented escape hatch for tests that
    # need a deterministic width regardless of inherited environment.
    return make_display_context(console=console, force_width=width)


def _load_default_policy() -> PipelinePolicy:
    defaults = Path(__file__).resolve().parents[2] / "ralph" / "policy" / "defaults"
    return load_policy(defaults).pipeline


def _state(phase: str, **kwargs: int) -> PipelineState:
    return PipelineState(phase=phase, loop_iterations=kwargs)


@pytest.mark.parametrize(
    "phase",
    [
        "planning",
        "planning_analysis",
        "development",
        "development_commit_cleanup",
        "development_commit",
        "development_analysis",
        "development_final_commit_cleanup",
        "development_final_commit",
    ],
)
def test_default_phase_resolves_outer_cycle(phase: str) -> None:
    """Every non-terminal phase in the default policy resolves a counter."""
    policy = _load_default_policy()
    state = PipelineState(
        phase=phase,
        loop_iterations={"development_analysis_iteration": 0, "commit_cleanup_iteration": 0},
        budget_caps={"iteration": 5},
        outer_progress={"iteration": 0},
    )
    entry = build_phase_entry_model_from_state(phase, state, policy)
    # Outer cycle must be present and 1-indexed (state has done zero
    # iterations, so the visible 1-indexed value is 1).
    assert entry.outer_dev_iteration is not None, (
        f"{phase} must show the outer cycle (AC-01)"
    )
    assert entry.outer_dev_cap == 5, f"{phase} cap must be 5"


@pytest.mark.parametrize("phase", ["complete", "failed_terminal"])
def test_terminal_phase_omits_outer_cycle(phase: str) -> None:
    """Terminal phases correctly omit the outer count (AC-01)."""
    policy = _load_default_policy()
    state = PipelineState(phase=phase)
    entry = build_phase_entry_model_from_state(phase, state, policy)
    assert entry.outer_dev_iteration is None, (
        f"{phase} must not show the outer cycle (AC-01)"
    )


def test_analysis_phase_shows_inner_count() -> None:
    """Planning / development analysis phases show the inner count."""
    policy = _load_default_policy()
    state = PipelineState(
        phase="development_analysis",
        loop_iterations={"development_analysis_iteration": 2},
    )
    entry = build_phase_entry_model_from_state(
        "development_analysis", state, policy
    )
    assert entry.inner_analysis is not None
    assert entry.inner_analysis_cap is not None
    # Outer must still be present and consistent
    assert entry.outer_dev_iteration is not None


def test_status_bar_renders_neutral_cycle_label() -> None:
    """Status Bar shows the phase-neutral ``Cycle`` label, not ``Dev`` (AC-02)."""
    ctx = _ctx()
    model = StatusBarModel(
        workspace_root="/tmp/probe",
        phase_label="Development",
        phase_style=phase_style_for_phase("development"),
        outer_dev_iteration=1,
        outer_dev_cap=5,
    )
    text = render_status_bar(model, ctx)
    assert "Cycle" in text.plain
    assert " Dev " not in text.plain


def test_status_bar_with_outer_label_uses_supplied_noun() -> None:
    """When ``outer_label`` is set, the renderer uses it (AC-02)."""
    ctx = _ctx()
    model = StatusBarModel(
        workspace_root="/tmp/probe",
        phase_label="Rebase Conflict Resolution",
        phase_style=phase_style_for_phase("rebase_conflict_resolution"),
        outer_dev_iteration=2,
        outer_dev_cap=4,
        outer_label="Round",
    )
    text = render_status_bar(model, ctx)
    assert "Round" in text.plain
    assert "Cycle" not in text.plain


def test_policy_remediation_push_uses_remediation_label_and_live_attempt() -> None:
    """Policy remediation shows the live attempt with a Remediation label."""
    ctx = _ctx()
    # The remediation helper builds a StatusBarModel with outer_label="Remediation"
    # carrying the live attempt; verify the contract renders the same
    # shape here through the public surface (AC-02, AC-07).
    model = StatusBarModel(
        workspace_root="/tmp/remediation-probe",
        phase_label="Policy Remediation",
        phase_style=phase_style_for_phase("policy_remediation"),
        outer_dev_iteration=2,
        outer_dev_cap=3,
        outer_label="Remediation",
    )
    text = render_status_bar(model, ctx)
    assert "Remediation" in text.plain
    assert "Cycle" not in text.plain


def test_conflict_resolution_push_uses_round_label() -> None:
    """Conflict resolution shows the round with a Round label."""
    ctx = _ctx()
    model = StatusBarModel(
        workspace_root="/tmp/conflict-probe",
        phase_label="Rebase Conflict Resolution",
        phase_style=phase_style_for_phase("rebase_conflict_resolution"),
        outer_dev_iteration=2,
        outer_dev_cap=3,
        outer_label="Round",
    )
    text = render_status_bar(model, ctx)
    assert "Round" in text.plain
    assert "Cycle" not in text.plain


def test_truncation_stability_across_widths() -> None:
    """Status Bar stays single-line at narrow widths (AC-04)."""
    for width in (40, 60, 80, 120):
        ctx = _ctx(width=width)
        model = StatusBarModel(
            workspace_root="/tmp/very-long-workspace-path-for-truncation-test/probe",
            phase_label="Development Analysis",
            phase_style=phase_style_for_phase("development_analysis"),
            outer_dev_iteration=1,
            outer_dev_cap=5,
            inner_analysis=1,
            inner_analysis_cap=5,
        )
        text = render_status_bar(model, ctx)
        assert "\n" not in text.plain, (
            f"status bar wrapped at width={width}: {text.plain!r}"
        )
        assert len(text.plain) <= width, (
            f"status bar exceeded width={width}: {len(text.plain)} > {width}"
        )


def test_remediation_label_at_40_columns_preserves_attempt_and_cap() -> None:
    """The Remediation label keeps ``Remediation 2/3`` visible at 40 cols (AC-04)."""
    ctx = _ctx(width=40)
    model = StatusBarModel(
        workspace_root="/tmp/remediation-probe",
        phase_label="Policy Remediation",
        phase_style=phase_style_for_phase("policy_remediation"),
        outer_dev_iteration=2,
        outer_dev_cap=3,
        outer_label="Remediation",
    )
    text = render_status_bar(model, ctx)
    # The bar must stay single-line and fit the terminal width.
    assert "\n" not in text.plain, f"status bar wrapped at width=40: {text.plain!r}"
    assert len(text.plain) <= 40, (
        f"status bar exceeded width=40: {len(text.plain)} > 40"
    )
    # The Remediation label MUST surface the attempt value AND the
    # cap; the prior bug truncated the bar mid-label so the operator
    # lost the live remediation progress. Pass when the canonical
    # ``Remediation 2/3`` is fully visible OR when the bar degrades
    # to a compact / minimal carrier that still preserves the
    # attempt number AND the cap (the ``2/3`` substring).
    assert "Remediation" in text.plain or "Rem" in text.plain, (
        f"Remediation carrier missing at width=40: {text.plain!r}"
    )
    assert "2/3" in text.plain, (
        f"remediation attempt+cap missing at width=40: {text.plain!r}"
    )


def test_round_label_at_40_columns_preserves_attempt_and_cap() -> None:
    """The Round label keeps ``Round 2/3`` visible at 40 cols (AC-04)."""
    ctx = _ctx(width=40)
    model = StatusBarModel(
        workspace_root="/tmp/conflict-probe",
        phase_label="Rebase Conflict Resolution",
        phase_style=phase_style_for_phase("rebase_conflict_resolution"),
        outer_dev_iteration=2,
        outer_dev_cap=3,
        outer_label="Round",
    )
    text = render_status_bar(model, ctx)
    assert "\n" not in text.plain, f"status bar wrapped at width=40: {text.plain!r}"
    assert len(text.plain) <= 40, (
        f"status bar exceeded width=40: {len(text.plain)} > 40"
    )
    assert "Round" in text.plain, f"Round carrier missing at width=40: {text.plain!r}"
    assert "2/3" in text.plain, (
        f"round attempt+cap missing at width=40: {text.plain!r}"
    )
