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
+ conflict-resolution push helpers. The walkthrough asserts EXACT
counter values (not just non-None), so an off-by-one regression in
``_find_commit_counter_from_phase`` / ``_build_phase_entry_model_from_state``
/ the ``+1`` 1-indexing / ``outer_label`` propagation trips the test.
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
from ralph.pipeline.conflict_resolution.status import push_conflict_status_bar
from ralph.pipeline.phase_transition import (
    _resolve_analysis_cap,
    build_phase_entry_model_from_state,
)
from ralph.pipeline.state import PipelineState
from ralph.policy.loader import load_policy
from ralph.project_policy import cli_integration
from ralph.workspace.scope import WorkspaceScope

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
    """Every non-terminal phase in the default policy resolves a counter.

    Asserts the EXACT visible value (== 1, the 1-indexed label when
    zero iterations have completed) plus cap == 5. An off-by-one
    regression in ``_build_phase_entry_model_from_state`` (which
    converts the 0-indexed ``state.get_outer_progress`` to a 1-indexed
    display label) would trip this assertion.
    """
    policy = _load_default_policy()
    state = PipelineState(
        phase=phase,
        loop_iterations={"development_analysis_iteration": 0, "commit_cleanup_iteration": 0},
        budget_caps={"iteration": 5},
        outer_progress={"iteration": 0},
    )
    entry = build_phase_entry_model_from_state(phase, state, policy)
    # Outer cycle must be present and exactly 1 (1-indexed; zero
    # completed iterations maps to visible ``1``). Catches off-by-one
    # regressions in the +1 display indexing.
    assert entry.outer_dev_iteration == 1, (
        f"{phase} must show the outer cycle as 1 (zero-indexed state "
        f"+ 1 display label); got {entry.outer_dev_iteration} (AC-01)"
    )
    assert entry.outer_dev_cap == 5, f"{phase} cap must be 5"


@pytest.mark.parametrize(
    ("phase", "completed_iterations", "expected_visible"),
    [
        ("development_analysis", 0, 1),
        ("development_analysis", 1, 2),
        ("development_analysis", 2, 3),
        ("development_analysis", 5, 6),
        ("development_analysis", 9, 10),
        ("development_analysis", 10, 10),
        ("development_analysis", 50, 10),
    ],
)
def test_analysis_phase_inner_count_exact_value(
    phase: str, completed_iterations: int, expected_visible: int
) -> None:
    """Analysis phases show the exact inner cycle value (1-indexed).

    The analysis counter is built from
    ``AnalysisLoopCounter.display_iteration`` which is
    ``min(max(completed, 0), max(cap - 1, 0)) + 1`` -- saturating at
    ``cap`` when ``completed >= cap``. Pinning the exact mapping
    catches off-by-one regressions AND saturation bugs in the analysis
    counter. The default ``development_analysis_iteration`` cap is
    10 (see ``ralph/policy/defaults/pipeline.toml``); this test
    pins the canonical default cap, not a synthesized value.
    """
    policy = _load_default_policy()
    cap = _resolve_analysis_cap("development_analysis_iteration", policy)
    assert cap == 10, (
        f"the default policy's development_analysis cap is 10 in "
        f"the canonical default policy; the test parametrize row "
        f"above must match the live policy; got {cap}"
    )
    state = PipelineState(
        phase=phase,
        loop_iterations={"development_analysis_iteration": completed_iterations},
        budget_caps={"iteration": 5},
        outer_progress={"iteration": 0},
    )
    entry = build_phase_entry_model_from_state(phase, state, policy)
    assert entry.inner_analysis == expected_visible, (
        f"{phase} with completed={completed_iterations} must surface "
        f"inner_analysis={expected_visible}; got {entry.inner_analysis}"
    )
    # Outer must also be present and 1-indexed consistently
    assert entry.outer_dev_iteration == 1, (
        f"{phase} must show outer cycle as 1; got {entry.outer_dev_iteration}"
    )


@pytest.mark.parametrize("phase", ["complete", "failed_terminal"])
def test_terminal_phase_omits_outer_cycle(phase: str) -> None:
    """Terminal phases correctly omit the outer count (AC-01).

    Terminal phases self-loop (``on_success`` -> self), so the trace
    exhausts the visited set and the function returns ``None``. The
    visible cycle field is therefore ``None`` and the status bar's
    outer formatter skips the segment.
    """
    policy = _load_default_policy()
    state = PipelineState(phase=phase)
    entry = build_phase_entry_model_from_state(phase, state, policy)
    assert entry.outer_dev_iteration is None, (
        f"{phase} must not show the outer cycle (AC-01); "
        f"got {entry.outer_dev_iteration}"
    )
    assert entry.outer_dev_cap is None, (
        f"{phase} must not show the outer cap (AC-01); "
        f"got {entry.outer_dev_cap}"
    )


def test_status_bar_renders_elapsed_time_and_agent_identity_when_space_allows() -> None:
    """AC-01: optional run context is labeled and survives no-color rendering."""
    model = StatusBarModel(
        workspace_root="/tmp/probe",
        phase_label="Development",
        phase_style=phase_style_for_phase("development"),
        outer_dev_iteration=1,
        outer_dev_cap=5,
        elapsed_seconds=65.0,
        agent_name="codex",
    )
    text = render_status_bar(model, _ctx())
    assert "Time 01:05" in text.plain
    assert "Agent codex" in text.plain


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


class _CapturingDisplay:
    """Lightweight ParallelDisplay stand-in for push-helper black-box tests.

    Captures the most-recent StatusBarModel the production helper
    pushed via ``update_status_bar`` so the test can assert on the
    EXACT production wiring (no synthesizing desired models in the
    test). Defensive against exceptions raised inside the helper --
    the helper itself is documented non-fatal, but the test should
    not blow up the rest of the suite if a regression raises.

    Note: this is NOT a real ``ParallelDisplay`` because that class
    uses ``__slots__`` and its ``update_status_bar`` is a method, not
    an instance attribute, so it cannot be subclassed and patched in
    place without disturbing the class's invariants. The push helpers
    we exercise here (``_push_remediation_status_bar`` and
    ``push_conflict_status_bar``) only call ``getattr(display,
    "update_status_bar", None)``, so a stub with the same contract
    is a faithful production substitute.
    """

    def __init__(self) -> None:
        self.last_model: StatusBarModel | None = None
        self.update_call_count: int = 0

    def update_status_bar(self, model: object) -> None:
        if isinstance(model, StatusBarModel):
            self.last_model = model
        self.update_call_count += 1


def test_remediation_push_helper_produces_correct_model() -> None:
    """Drive the real ``_push_remediation_status_bar`` and assert exact values.

    Uses the production helper, not a synthesized StatusBarModel, so
    the test fails if the helper regresses (e.g. the bug where
    ``outer_dev_iteration=1`` was hardcoded). Pins the live attempt
    and cap, the outer_label propagation, and the rendered text.
    """
    capture = _CapturingDisplay()
    scope = WorkspaceScope(
        root=Path("/tmp/remediation-walkthrough-probe"),
        allowed_roots=frozenset({Path("/tmp/remediation-walkthrough-probe")}),
    )
    cli_integration._push_remediation_status_bar(
        capture,
        scope,
        max_attempts=3,
        attempt=2,
        elapsed_seconds=65.0,
        agent_name="policy-agent",
    )
    assert capture.update_call_count == 1
    assert capture.last_model is not None
    model = capture.last_model
    assert model.outer_dev_iteration == 2, (
        f"remediation push must surface live attempt 2; got {model.outer_dev_iteration}"
    )
    assert model.outer_dev_cap == 3
    assert model.outer_label == "Remediation", (
        f"remediation label must be 'Remediation'; got {model.outer_label!r}"
    )
    assert model.elapsed_seconds == 65.0
    assert model.agent_name == "policy-agent"
    # Render and confirm the bar shows ``Remediation 2/3`` exactly.
    text = render_status_bar(model, _ctx())
    assert "Remediation" in text.plain
    assert "2/3" in text.plain
    assert "Time 01:05" in text.plain
    assert "Agent policy-agent" in text.plain
    assert "Cycle" not in text.plain


def test_conflict_resolution_push_helper_produces_correct_model() -> None:
    """Drive the real ``push_conflict_status_bar`` and assert exact values.

    Pins the live round index and round_cap, the outer_label
    propagation, and the rendered text. Catches regressions where
    the conflict-resolution phase gets mislabeled or pinned to a
    hardcoded ``Dev`` value.
    """
    capture = _CapturingDisplay()
    push_conflict_status_bar(
        capture,
        Path("/tmp/conflict-walkthrough-probe"),
        target="main",
        round_index=2,
        round_cap=3,
        elapsed_seconds=3_661.0,
        agent_name="resolver-agent",
    )
    assert capture.update_call_count == 1
    assert capture.last_model is not None
    model = capture.last_model
    assert model.outer_dev_iteration == 2, (
        f"conflict push must surface round 2; got {model.outer_dev_iteration}"
    )
    assert model.outer_dev_cap == 3
    assert model.outer_label == "Round", (
        f"conflict label must be 'Round'; got {model.outer_label!r}"
    )
    assert model.elapsed_seconds == 3_661.0
    assert model.agent_name == "resolver-agent"
    # Render and confirm the bar shows ``Round 2/3`` exactly.
    text = render_status_bar(model, _ctx())
    assert "Round" in text.plain
    assert "2/3" in text.plain
    assert "Time 1:01:01" in text.plain
    assert "Agent resolver-agent" in text.plain
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
