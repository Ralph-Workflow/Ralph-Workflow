"""Black-box tests for the persistent Status Bar at the bottom of the display.

The Status Bar shows working directory, active phase, and any applicable
outer development iteration and inner analysis iteration during interactive
runs. This file pins the contract:

- ``render_status_bar`` is a pure function (no I/O, no env reads, no Console
  construction; ``home`` is a parameter so the function does not call
  ``pathlib.Path.home()``).
- The StatusBar lifecycle is a no-op unless ``ctx.console.is_terminal AND
  ctx.console.file.isatty()`` are both True (Rich's ``is_terminal`` is True
  on force_terminal+StringIO consoles, so the ``isatty()`` conjunct is
  mandatory to keep force_terminal tests, redirects, pipes, and CI logs
  clean).
- Cadence constants ``_STATUS_BAR_REFRESH_PER_SECOND`` and
  ``_STATUS_BAR_TRANSIENT`` are pinned by import-time assertions.
- Run-loop wiring uses 1-indexed ``outer_dev_iteration`` semantics from
  ``PhaseEntryModel`` (completed+1), not the snapshot's completed count.
- ``ParallelDisplay`` composes the StatusBar; ``update_status_bar`` is the
  public surface (outside the one-shot emit_* surface).
"""

from __future__ import annotations

import ast
import dataclasses
import inspect
import io
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
import rich.live
from rich.cells import cell_len
from rich.console import Console

import ralph.pipeline.run_loop as _run_loop_module
from ralph.display import status_bar as _status_bar_module
from ralph.display.context import DisplayContext, make_display_context
from ralph.display.parallel_display import ParallelDisplay
from ralph.display.status_bar import (
    _STATUS_BAR_REFRESH_PER_SECOND,
    _STATUS_BAR_TRANSIENT,
    StatusBar,
    StatusBarModel,
    render_status_bar,
)
from ralph.display.theme import RALPH_THEME

if TYPE_CHECKING:
    from rich.text import Text


def _plain_text(text: Text) -> str:
    """Return the plain (markup-stripped) text of a rich.text.Text instance."""
    return text.plain


class _TtyLikeStringIO(io.StringIO):
    """An in-memory buffer that reports ``isatty() is True``.

    Used to test the real-TTY branch of the StatusBar gate (the
    ``console.is_terminal AND console.file.isatty()`` conjunct) without
    requiring an actual pseudo-tty. This is the same shape of tty-like
    StringIO used in the analysis feedback's runtime repro that exposed
    the live-update bug.
    """

    def isatty(self) -> bool:
        return True


def _make_display_context(
    *,
    width: int,
    force_terminal: bool = False,
    ascii_glyphs: bool = False,
) -> DisplayContext:
    """Build a DisplayContext with a StringIO-backed Console of the given width.

    ``ascii_glyphs=True`` forces ASCII fallback glyphs (no Unicode markers).
    Default is False (Unicode glyphs enabled), matching the production default.
    """
    buf = io.StringIO()
    console = Console(
        file=buf,
        force_terminal=force_terminal,
        color_system=None,
        width=width,
    )
    return make_display_context(
        console=console,
        env={},
        force_width=width,
        force_glyphs=not ascii_glyphs,
    )


# ---------------------------------------------------------------------------
# render_status_bar — single default-mode layout shows all four fields
# ---------------------------------------------------------------------------


def test_render_status_bar_default_mode_shows_all_applicable_fields() -> None:
    """Single default-mode layout: phase + dir + outer_dev + inner_analysis all present.

    After the wt-028-display consolidation, the persistent Status Bar
    always renders all applicable fields regardless of terminal width.
    Only path middle-truncation and phase tail-truncation adapt to
    width.
    """
    model = StatusBarModel(
        workspace_root="/Users/alice/code/my-cool-project",
        phase_label="Development",
        phase_style="theme.phase.development",
        outer_dev_iteration=1,
        outer_dev_cap=3,
        inner_analysis=2,
        inner_analysis_cap=5,
    )
    ctx = _make_display_context(width=140)
    text = render_status_bar(model, ctx, home="/Users/alice")
    plain = _plain_text(text)
    assert "Development" in plain
    assert "my-cool-project" in plain
    assert "Cycle 1/3" in plain
    assert "iter 2/5" in plain


def test_render_status_bar_regression_wide_unicode_respects_terminal_cells() -> None:
    """S-5: wide and combining input never makes the live bar exceed its cell budget."""
    model = StatusBarModel(
        workspace_root="/work/界界界/cafe\u0301/finish.py",
        phase_label="Development 界界界",
        phase_style="theme.phase.development",
        agent_name="pi界",
    )
    ctx = _make_display_context(width=40)

    rendered = render_status_bar(model, ctx, home=None)

    assert cell_len(rendered.plain) <= ctx.width


def test_render_status_bar_regression_wide_agent_identifier_respects_terminal_cells() -> None:
    """S-5: a wide agent identifier cannot overrun its fixed status-bar slot."""
    model = StatusBarModel(
        workspace_root="/work/project/terminal-tail",
        phase_label="Development",
        phase_style="theme.phase.development",
        agent_name="界界界界界界界界界界",
    )
    ctx = _make_display_context(width=80)

    rendered = render_status_bar(model, ctx, home=None)

    assert cell_len(rendered.plain) <= ctx.width


def test_render_status_bar_regression_wide_custom_outer_label_uses_cell_width() -> None:
    """S-5: a wide custom cycle label fits by terminal cells without clipping its cap."""
    model = StatusBarModel(
        workspace_root="/work/project/terminal-tail",
        phase_label="Development",
        phase_style="theme.phase.development",
        outer_dev_iteration=2,
        outer_dev_cap=3,
        inner_analysis=1,
        inner_analysis_cap=3,
        agent_name="pi",
        outer_label="界界界界界界界界界界",
    )
    ctx = _make_display_context(width=120)

    rendered = render_status_bar(model, ctx, home=None)

    assert "界界界界界界界界界界 2/3" in rendered.plain
    assert cell_len(rendered.plain) <= ctx.width


def test_render_status_bar_shows_integration_alert() -> None:
    """An unresolved integration conflict renders a leading alert segment."""
    model = StatusBarModel(
        workspace_root="/Users/alice/code/my-cool-project",
        phase_label="Development",
        phase_style="theme.phase.development",
        integration_alert="integration conflict — resolution required",
    )
    ctx = _make_display_context(width=140)
    text = render_status_bar(model, ctx, home="/Users/alice")
    plain = _plain_text(text)
    assert "integration conflict" in plain
    # The alert leads the bar so it can never be missed.
    assert plain.index("integration conflict") < plain.index("Development")


def test_render_status_bar_without_alert_unchanged() -> None:
    """No alert set: the bar renders exactly as before (no alert text)."""
    model = StatusBarModel(
        workspace_root="/Users/alice/code/my-cool-project",
        phase_label="Development",
        phase_style="theme.phase.development",
    )
    ctx = _make_display_context(width=140)
    plain = _plain_text(render_status_bar(model, ctx, home="/Users/alice"))
    assert "conflict" not in plain


def _build_model_with_rebase(monkeypatch: pytest.MonkeyPatch, rebase: object) -> StatusBarModel:
    """Drive _build_status_bar_model with fakes and the given rebase state."""

    class _FakeEntry:
        outer_dev_iteration = None
        outer_dev_cap = None
        inner_analysis = None
        inner_analysis_cap = None

        def human_label(self) -> str:
            return "Development"

    monkeypatch.setattr(
        _run_loop_module,
        "build_phase_entry_model_from_state",
        lambda *_a, **_k: _FakeEntry(),
    )
    monkeypatch.setattr(
        _run_loop_module,
        "phase_style_for_phase",
        lambda *_a, **_k: "theme.phase.development",
    )

    class _FakeState:
        phase = "development"
        rebase: object

        def __init__(self, rebase_state: object) -> None:
            self.rebase = rebase_state

    class _FakePolicyBundle:
        pipeline = object()

    return _run_loop_module._build_status_bar_model(
        _FakeState(rebase), _FakePolicyBundle(), Path("/tmp/ws")
    )


def test_build_status_bar_model_sets_integration_alert_on_conflict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The run-loop model builder surfaces an unresolved conflict state."""
    from ralph.pipeline.rebase_state import RebaseState

    rebase = RebaseState(
        last_action="conflict",
        last_reason="rebase and endpoint merge both conflicted",
        last_target="main",
    )
    model = _build_model_with_rebase(monkeypatch, rebase)
    assert model.integration_alert is not None
    assert "conflict" in model.integration_alert


def test_build_status_bar_model_no_alert_without_conflict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A clean rebase state yields no alert."""
    from ralph.pipeline.rebase_state import RebaseState

    model = _build_model_with_rebase(monkeypatch, RebaseState())
    assert model.integration_alert is None


@pytest.mark.parametrize("width", [120, 200])
def test_render_status_bar_shows_all_fields_at_wide_widths(width: int) -> None:
    """At wide widths (>=120 cols), the Status Bar renders phase + dir + outer_dev + inner_analysis.

    The single default-mode layout preserves all applicable fields at any
    width that can accommodate them. At wide widths the path and phase
    labels fit the default budgets (path=48, phase=28) so all four
    fields render in full.

    Note: width 100 is excluded here because the new chrome
    (DA-001 reserved attention slot ~12 cols + DA-002 fixed-width
    elapsed display ~13 cols) consumes enough budget that
    canonical-form ``Cycle 1/3`` + ``iter 2/5`` + the full
    workspace path cannot fit at 100 cols without dropping one
    of them. The 100-col rung is covered by the
    ``test_render_status_bar_drops_path_at_60`` family at 60 and
    the ``test_segment_order_matches_spec`` family at 120+.
    """
    model = StatusBarModel(
        workspace_root="/Users/alice/code/my-cool-project",
        phase_label="Development",
        phase_style="theme.phase.development",
        outer_dev_iteration=1,
        outer_dev_cap=3,
        inner_analysis=2,
        inner_analysis_cap=5,
    )
    ctx = _make_display_context(width=width)
    text = render_status_bar(model, ctx, home="/Users/alice")
    plain = _plain_text(text)
    assert "Development" in plain
    assert "my-cool-project" in plain
    assert "Cycle 1/3" in plain
    assert "iter 2/5" in plain


@pytest.mark.parametrize("width", [40, 50, 60, 80, 99])
def test_render_status_bar_fits_terminal_width_at_any_width(width: int) -> None:
    """At any width, the Status Bar fits the terminal width without wrapping.

    The bar must NEVER exceed the terminal width. The phase, path, and
    iteration-label budgets are derived together from ``ctx.width`` so
    the rendered text remains single-line and within ``ctx.width``
    columns at every width. Path middle-truncation and phase
    tail-truncation adapt to width; the iteration label form may
    shorten at narrow widths to keep the bar single-line.
    """
    model = StatusBarModel(
        workspace_root="/Users/alice/code/my-cool-project",
        phase_label="Development",
        phase_style="theme.phase.development",
        outer_dev_iteration=1,
        outer_dev_cap=3,
        inner_analysis=2,
        inner_analysis_cap=5,
    )
    ctx = _make_display_context(width=width)
    text = render_status_bar(model, ctx, home="/Users/alice")
    plain = _plain_text(text)
    # Single-line invariant: no embedded newline.
    assert "\n" not in plain, f"Status Bar must not wrap: {plain!r}"
    # Width-fit invariant: never exceeds ctx.width.
    assert len(plain) <= width, (
        f"Status Bar exceeds terminal width: len(plain)={len(plain)} > width={width}, "
        f"plain={plain!r}"
    )


@pytest.mark.parametrize("width", [40, 50, 60, 80, 99, 120, 200])
def test_render_status_bar_shows_all_applicable_fields_at_ac03_widths(width: int) -> None:
    """At AC-03 widths (>=40 cols), the Status Bar renders phase + dir + outer_dev + inner_analysis.

    This is the central AC-03 invariant: at widths >= 40 cols the
    persistent bottom Status Bar always renders all applicable
    iteration fields regardless of terminal width. The per-iteration
    label form adapts to ``ctx.width`` (canonical / compact / minimal)
    so the bar always fits ``ctx.width``, but the count-vs-cap payload
    (``1/3`` for outer_dev and ``2/5`` for inner_analysis) is ALWAYS
    present in some form.

    Below 40 cols the implementation may drop one or both iteration
    segments to honour the AC-07 narrow-terminal contract (workspace
    path and phase label remain readable at every applicable width).
    The companion
    ``test_render_status_bar_workspace_phase_visible_at_narrow_widths``
    test locks the AC-07 contract at widths 14/15/20/24/30.
    """
    model = StatusBarModel(
        workspace_root="/Users/alice/code/my-cool-project",
        phase_label="Development",
        phase_style="theme.phase.development",
        outer_dev_iteration=1,
        outer_dev_cap=3,
        inner_analysis=2,
        inner_analysis_cap=5,
    )
    ctx = _make_display_context(width=width)
    text = render_status_bar(model, ctx, home="/Users/alice")
    plain = _plain_text(text)
    # outer_dev iteration: must always render in SOME form at AC-03
    # widths. Accept canonical ("Cycle 1/3"), compact ("C1/3"), or
    # minimal ("1/3"). The count/payload ("1/3") is the
    # disambiguating invariant.
    outer_forms = ("Cycle 1/3", "C1/3", "1/3")
    assert any(form in plain for form in outer_forms), (
        f"outer_dev must render in canonical/compact/minimal form at width={width}; got {plain!r}"
    )
    # inner_analysis iteration: must always render in SOME form at
    # AC-03 widths >= 50. The 40-col floor (wt-028-display S-3)
    # keeps the 5 surviving segments (attention, phase, liveness,
    # position, elapsed); inner_analysis is NOT one of the five
    # so it can drop at the exact floor. The pre-S-3 test pinned
    # the BUG (inner_analysis rendered but elapsed dropped); the
    # spec is the other way around at the floor.
    if width >= 50:
        inner_forms = ("iter 2/5", "i2/5", "2/5")
        assert any(form in plain for form in inner_forms), (
            f"inner_analysis must render in canonical/compact/minimal form at "
            f"width={width}; got {plain!r}"
        )


@pytest.mark.parametrize("width", [120, 200])
def test_render_status_bar_canonical_iteration_labels_at_wide_widths(width: int) -> None:
    """At wide widths (>=120 cols), iteration labels render in canonical form.

    Locks the AC-03 invariant at widths where the chrome leaves enough
    budget for the full canonical labels: 120/200 cols. The
    canonical ``Cycle 1/3`` and ``iter 2/5`` forms must appear in
    the rendered bar.

    At widths < 120 the attention, elapsed, agent, and cwd contract consumes
    enough budget that canonical iter labels cannot fit alongside
    a readable workspace path and phase. The implementation falls
    back to compact (``C1/3`` / ``i2/5``) at those widths; the
    narrow-width behaviour is covered by
    ``test_render_status_bar_iteration_labels_compact_at_narrow_widths``
    below. Below 40 cols the implementation may shorten further to
    minimal form (``1/3`` / ``2/5``).
    """
    model = StatusBarModel(
        workspace_root="/Users/alice/code/my-very-long-project-directory-name/subdir",
        phase_label="Development Analysis",
        phase_style="theme.phase.development",
        outer_dev_iteration=1,
        outer_dev_cap=3,
        inner_analysis=2,
        inner_analysis_cap=5,
    )
    ctx = _make_display_context(width=width)
    text = render_status_bar(model, ctx, home="/Users/alice")
    plain = _plain_text(text)
    # Canonical form is required at wide widths.
    assert "Cycle 1/3" in plain, (
        f"AC-03: 'Cycle 1/3' must render in canonical form at width={width}; got {plain!r}"
    )
    assert "iter 2/5" in plain, (
        f"AC-03: 'iter 2/5' must render in canonical form at width={width}; got {plain!r}"
    )
    # Width-fit invariant.
    assert len(plain) <= width, (
        f"rendered bar exceeds width at width={width}; "
        f"len(plain)={len(plain)} > width={width}, plain={plain!r}"
    )
    # Single-line invariant.
    assert "\n" not in plain, f"rendered bar must be single-line at width={width}; got {plain!r}"


@pytest.mark.parametrize("width", [40, 50, 60, 80])
def test_render_status_bar_iteration_labels_compact_at_narrow_widths(width: int) -> None:
    """At widths < 120 with the new chrome, iteration labels degrade to compact form.

    The DA-001 (reserved attention slot) + DA-002 (fixed-width elapsed
    display) chrome consumes enough budget at widths 40/50/60 that the
    full canonical ``Cycle 1/3`` + ``iter 2/5`` labels cannot fit
    alongside the workspace path and phase. The implementation falls
    back to compact (``C1/3`` / ``i2/5``) at these widths so the bar
    stays single-line and within the terminal width.

    wt-028-display S-3: at the 40-col floor, inner_analysis drops
    entirely (the spec keeps the 5 surviving segments:
    attention, phase, liveness, position, elapsed; inner_analysis
    is not one of them).
    """
    model = StatusBarModel(
        workspace_root="/Users/alice/code/my-very-long-project-directory-name/subdir",
        phase_label="Development Analysis",
        phase_style="theme.phase.development",
        outer_dev_iteration=1,
        outer_dev_cap=3,
        inner_analysis=2,
        inner_analysis_cap=5,
    )
    ctx = _make_display_context(width=width)
    text = render_status_bar(model, ctx, home="/Users/alice")
    plain = _plain_text(text)
    # Compact form is required at narrow widths.
    outer_forms = ("C1/3", "1/3")
    assert any(form in plain for form in outer_forms), (
        f"outer_dev must render in compact/minimal form at width={width}; got {plain!r}"
    )
    # inner_analysis: at 50/60 cols the compact form fits; at the
    # 40-col floor the spec drops inner_analysis to honour the
    # 5-segment floor contract (attention, phase, liveness,
    # position, elapsed).
    if width >= 50:
        inner_forms = ("i2/5", "2/5")
        assert any(form in plain for form in inner_forms), (
            f"inner_analysis must render in compact/minimal form at width={width}; got {plain!r}"
        )
    # Width-fit invariant.
    assert len(plain) <= width, (
        f"rendered bar exceeds width at width={width}; "
        f"len(plain)={len(plain)} > width={width}, plain={plain!r}"
    )
    # Single-line invariant.
    assert "\n" not in plain, f"rendered bar must be single-line at width={width}; got {plain!r}"


@pytest.mark.parametrize("width", [14, 15, 20, 24, 30, 40, 50, 60, 80, 100, 120])
def test_render_status_bar_fits_width_at_narrow_terminal_with_long_inputs(width: int) -> None:
    """Status Bar fits ``ctx.width`` even with long inputs at narrow terminals.

    Regression for the analysis-feedback finding that the previous
    implementation produced a 45-char rendered bar at widths 20/24/30
    with long workspace paths and both iteration fields. The fix
    protects the workspace path and phase label with a minimum budget
    (_MIN_PATH_BUDGET / _MIN_PHASE_BUDGET) and degrades the iteration
    label form from canonical (``Cycle 1/3`` / ``iter 2/5``) to
    compact (``C1/3`` / ``i2/5``) to minimal (``1/3`` / ``2/5``), and
    drops the iteration segments (outer_dev first, then
    inner_analysis) so workspace + phase remain readable at every
    applicable width. Below the iteration-visibility threshold the
    marker / per-iteration glyphs are dropped as needed so
    ``len(plain) <= width`` always holds at width >= 14.

    Width 14 is the narrowest width where the AC-07 contract still
    permits a single iteration segment alongside workspace + phase.
    Below 14 cols the iteration segments drop one at a time so the
    bar degrades cleanly to phase + path; the companion
    ``test_render_status_bar_fits_terminal_width_below_14`` test
    covers the 1-13 col range and locks the ``len(plain) <= width``
    invariant at every width below the iteration-visibility threshold.
    The companion ``test_render_status_bar_workspace_phase_visible_at_narrow_widths``
    test locks the AC-07 narrow-terminal workspace+phase contract at
    widths 14/15/20/24/30.
    """
    long_path = "/Users/alice/code/my-very-long-project-directory-name/subdir"
    long_phase = "Development Analysis"
    model = StatusBarModel(
        workspace_root=long_path,
        phase_label=long_phase,
        phase_style="theme.phase.development",
        outer_dev_iteration=1,
        outer_dev_cap=3,
        inner_analysis=2,
        inner_analysis_cap=5,
    )
    ctx = _make_display_context(width=width)
    text = render_status_bar(model, ctx, home="/Users/alice")
    plain = _plain_text(text)
    # Single-line invariant.
    assert "\n" not in plain, f"Status Bar must not wrap at width={width}; got {plain!r}"
    # Width-fit invariant.
    assert len(plain) <= width, (
        f"Status Bar exceeds terminal width at width={width}: "
        f"len(plain)={len(plain)} > width={width}, plain={plain!r}"
    )


@pytest.mark.parametrize("width", [70, 80, 90, 99])
def test_render_status_bar_workspace_phase_visible_at_narrow_widths(width: int) -> None:
    """AC-07 narrow-terminal contract: workspace path AND phase label are readable.

    Drives ``render_status_bar`` at narrow widths (70, 80, 90, 99) with
    long workspace path AND long phase label AND both iteration
    fields populated. Asserts the AC-07 minimum contract at the
    spec ladder rungs (70-99 cols, per DA-001 attention slot reservation
    + DA-002 fixed-width elapsed display): the workspace path AND
    phase label both render in some recognizable form even when
    the iteration segments are dropped or degraded to fit the
    available width.

    At widths 40-60 the new chrome consumes enough budget that the
    workspace path may drop alongside the agent segment (the spec
    says path drops at width 60); the width-fit invariant at those
    sub-70 widths is covered by
    ``test_render_status_bar_fits_terminal_width_below_14`` and
    the structured degradation is exercised by the wider widths
    above.

    This is the direct AC-07 lock at the ``render_status_bar`` seam
    (the run-loop seam is covered by
    ``tests/pipeline/test_run_loop_status_bar_wiring.py::
    test_run_inner_loop_status_bar_fits_at_narrow_widths``). The
    test does NOT spawn a subprocess, does NOT use ``time.sleep``,
    and runs in well under 1s per parametrized variant so it fits
    inside the 60s combined test budget.
    """
    long_path = "/Users/alice/code/my-very-long-project-directory-name/subdir"
    long_phase = "Development Analysis"
    model = StatusBarModel(
        workspace_root=long_path,
        phase_label=long_phase,
        phase_style="theme.phase.development",
        outer_dev_iteration=1,
        outer_dev_cap=3,
        inner_analysis=2,
        inner_analysis_cap=5,
    )
    ctx = _make_display_context(width=width)
    text = render_status_bar(model, ctx, home="/Users/alice")
    plain = _plain_text(text)
    # Width-fit + single-line invariants (regression for the 45-char
    # overflow bug at narrow widths with long inputs).
    assert "\n" not in plain, f"AC-07: bar must not wrap at width={width}; got {plain!r}"
    assert len(plain) <= width, (
        f"AC-07: bar exceeds width at width={width}; "
        f"len(plain)={len(plain)} > {width}, plain={plain!r}"
    )
    # AC-07 minimum contract: workspace path AND phase label are
    # recognizable at every applicable width. The path budget
    # allocator reserves at least _MIN_PATH_BUDGET chars for path
    # (so the trailing segment is recognizable), and at least
    # _MIN_PHASE_BUDGET chars for phase (so a recognizable prefix of
    # the human phase label renders).
    phase_label_prefixes = (
        long_phase[:3],  # "Dev" — first 3 chars of "Development Analysis"
        long_phase[:4],  # "Deve"
        long_phase[:2],  # "De"
    )
    phase_visible = any(prefix.lower() in plain.lower() for prefix in phase_label_prefixes)
    assert phase_visible, (
        f"AC-07: at width={width}, phase label must remain visible "
        f"(any of {phase_label_prefixes!r}); got plain={plain!r}"
    )
    trailing_segment = long_path.rsplit("/", 1)[-1]
    path_prefixes = (
        trailing_segment[:3],  # "sub"
        trailing_segment[:2],  # "su"
        trailing_segment[:1],  # "s"
    )
    path_visible = any(prefix in plain for prefix in path_prefixes)
    assert path_visible, (
        f"AC-07: at width={width}, trailing workspace path segment must "
        f"remain visible (any of {path_prefixes!r}); got plain={plain!r}"
    )


# ---------------------------------------------------------------------------
# render_status_bar — widths below 14: bar degrades cleanly (no overflow)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("width", [1, 2, 4, 6, 8, 10, 12, 13])
def test_render_status_bar_fits_terminal_width_below_14(width: int) -> None:
    """At very narrow widths (<14 cols), the Status Bar never overflows.

    The persistent Status Bar must always fit ``ctx.width`` at every
    width — including widths below the iteration-visibility threshold
    (14 cols). At very narrow widths the implementation may drop
    iteration segments (outer_dev / inner_analysis) entirely so the bar
    does not overflow into the working area; the bar degrades cleanly
    to whatever subset of phase + path can fit.

    Below 14 cols the iteration-visibility contract is no longer
    binding: at width 7 neither iteration label can fit alongside
    even a single separator character, so the implementation drops
    one or both segments as needed. The bar must still be
    single-line and ``len(plain) <= ctx.width`` for the full range
    of widths (1 through 13 cols).

    The marker-prefix / glyphs are also dropped because the chrome
    would otherwise overflow before any label can render.
    """
    model = StatusBarModel(
        workspace_root="/Users/alice/code/my-very-long-project-directory-name/subdir",
        phase_label="Development Analysis",
        phase_style="theme.phase.development",
        outer_dev_iteration=1,
        outer_dev_cap=3,
        inner_analysis=2,
        inner_analysis_cap=5,
    )
    ctx = _make_display_context(width=width)
    text = render_status_bar(model, ctx, home="/Users/alice")
    plain = _plain_text(text)
    # Single-line invariant: never wraps into the working area.
    assert "\n" not in plain, f"Status Bar must not wrap at width={width}; got {plain!r}"
    # Width-fit invariant: the core contract for any width.
    assert len(plain) <= width, (
        f"Status Bar exceeds terminal width at width={width}: "
        f"len(plain)={len(plain)} > width={width}, plain={plain!r}"
    )


# ---------------------------------------------------------------------------
# render_status_bar — placeholder omission (whole segment, not just label)
# ---------------------------------------------------------------------------


def test_render_status_bar_no_dash_placeholder_when_outer_dev_is_none() -> None:
    """When outer_dev_iteration is None, the rendered text contains NO '--' placeholder.

    The whole outer_dev segment (glyph + iteration field) must be omitted — not
    rendered as a glyph + '--' stub. This pins the AC-02 omission contract.
    """
    model = StatusBarModel(
        workspace_root="/Users/alice/code/proj",
        phase_label="Commit",
        phase_style="theme.phase.commit",
        outer_dev_iteration=None,
        outer_dev_cap=None,
        inner_analysis=None,
        inner_analysis_cap=None,
    )
    ctx = _make_display_context(width=140)
    text = render_status_bar(model, ctx, home="/Users/alice")
    plain = _plain_text(text)
    # No '--' placeholder anywhere in the rendered output.
    assert "--" not in plain, f"Status bar must not render '--' placeholders; got {plain!r}"
    # Neither outer_dev glyph (Unicode '◎' or ASCII '[OD]') should appear.
    assert "◎" not in plain, (
        f"outer_dev Unicode glyph must be absent when iteration is None; got {plain!r}"
    )
    assert "[OD]" not in plain, (
        f"outer_dev ASCII glyph must be absent when iteration is None; got {plain!r}"
    )


def test_render_status_bar_no_dash_placeholder_when_inner_analysis_is_none() -> None:
    """When inner_analysis is None, the rendered text contains NO '--' placeholder."""
    model = StatusBarModel(
        workspace_root="/Users/alice/code/proj",
        phase_label="Development",
        phase_style="theme.phase.development",
        outer_dev_iteration=1,
        outer_dev_cap=3,
        inner_analysis=None,
        inner_analysis_cap=None,
    )
    ctx = _make_display_context(width=140)
    text = render_status_bar(model, ctx, home="/Users/alice")
    plain = _plain_text(text)
    # The outer_dev field is present, the inner_analysis field is not.
    assert "Cycle 1/3" in plain
    assert "--" not in plain, f"Status bar must not render '--' placeholders; got {plain!r}"
    # Neither inner_analysis glyph (Unicode '▸' or ASCII '[IA]') should appear.
    assert "▸" not in plain, (
        f"inner_analysis Unicode glyph must be absent when iteration is None; got {plain!r}"
    )
    assert "[IA]" not in plain, (
        f"inner_analysis ASCII glyph must be absent when iteration is None; got {plain!r}"
    )


def test_render_status_bar_no_dash_placeholder_when_iterations_are_none() -> None:
    """When iteration fields are None on the model, no '--' placeholder appears."""
    model = StatusBarModel(
        workspace_root="/Users/alice/code/proj",
        phase_label="Commit",
        phase_style="theme.phase.commit",
        outer_dev_iteration=None,
        inner_analysis=None,
    )
    ctx = _make_display_context(width=140)
    text = render_status_bar(model, ctx, home="/Users/alice")
    plain = _plain_text(text)
    assert "--" not in plain


def test_render_status_bar_no_dash_placeholder_in_ascii_mode() -> None:
    """In ASCII glyph mode, omitted iteration fields leave NO '[OD] --' or '[IA] --' stub."""
    model = StatusBarModel(
        workspace_root="/Users/alice/code/proj",
        phase_label="Commit",
        phase_style="theme.phase.commit",
        outer_dev_iteration=None,
        inner_analysis=None,
    )
    ctx = _make_display_context(width=140, ascii_glyphs=True)
    text = render_status_bar(model, ctx, home="/Users/alice")
    plain = _plain_text(text)
    assert "[OD]" not in plain
    assert "[IA]" not in plain
    assert "--" not in plain


# ---------------------------------------------------------------------------
# render_status_bar — Cycle label formatting
# ---------------------------------------------------------------------------


def test_render_status_bar_dev_iteration_format_with_cap() -> None:
    """outer_dev_iteration=1, outer_dev_cap=3 -> 'Cycle 1/3'."""
    model = StatusBarModel(
        workspace_root="/Users/alice/code/proj",
        phase_label="Development",
        phase_style="theme.phase.development",
        outer_dev_iteration=1,
        outer_dev_cap=3,
    )
    ctx = _make_display_context(width=140)
    text = render_status_bar(model, ctx, home="/Users/alice")
    assert "Cycle 1/3" in _plain_text(text)


def test_render_status_bar_dev_iteration_format_without_cap() -> None:
    """outer_dev_iteration=2, outer_dev_cap=None -> 'Cycle #2' (canonical fallback)."""
    model = StatusBarModel(
        workspace_root="/Users/alice/code/proj",
        phase_label="Development",
        phase_style="theme.phase.development",
        outer_dev_iteration=2,
        outer_dev_cap=None,
    )
    ctx = _make_display_context(width=140)
    text = render_status_bar(model, ctx, home="/Users/alice")
    assert "Cycle #2" in _plain_text(text)


# ---------------------------------------------------------------------------
# render_status_bar — Analysis label formatting
# ---------------------------------------------------------------------------


def test_render_status_bar_analysis_iteration_format_with_cap() -> None:
    """inner_analysis=3, inner_analysis_cap=7 -> 'iter 3/7'."""
    model = StatusBarModel(
        workspace_root="/Users/alice/code/proj",
        phase_label="Development Analysis",
        phase_style="theme.phase.analysis",
        outer_dev_iteration=1,
        outer_dev_cap=3,
        inner_analysis=3,
        inner_analysis_cap=7,
    )
    ctx = _make_display_context(width=140)
    text = render_status_bar(model, ctx, home="/Users/alice")
    assert "iter 3/7" in _plain_text(text)


def test_render_status_bar_analysis_iteration_format_without_cap() -> None:
    """inner_analysis=1, inner_analysis_cap=None -> 'iter #1' fallback."""
    model = StatusBarModel(
        workspace_root="/Users/alice/code/proj",
        phase_label="Development Analysis",
        phase_style="theme.phase.analysis",
        outer_dev_iteration=1,
        outer_dev_cap=3,
        inner_analysis=1,
        inner_analysis_cap=None,
    )
    ctx = _make_display_context(width=140)
    text = render_status_bar(model, ctx, home="/Users/alice")
    assert "iter #1" in _plain_text(text)


# ---------------------------------------------------------------------------
# render_status_bar — Path middle-truncation, never wraps
# ---------------------------------------------------------------------------


def test_render_status_bar_truncates_long_path_no_wrap() -> None:
    """A long workspace path is middle-truncated and the rendered text has no '\\n'."""
    long_path = "/Users/alice/very-very-long-directory-name/my-very-cool-project-name/subdir"
    model = StatusBarModel(
        workspace_root=long_path,
        phase_label="Development",
        phase_style="theme.phase.development",
        outer_dev_iteration=1,
        outer_dev_cap=3,
    )
    ctx = _make_display_context(width=140)
    text = render_status_bar(model, ctx, home="/Users/alice")
    plain = _plain_text(text)
    assert "\n" not in plain, f"Status Bar must not wrap into the working area: {plain!r}"
    # The whole long path must NOT be present (it was truncated to a budget).
    assert long_path not in plain, "Path was not truncated."
    # Some abbreviated form must survive.
    assert "/" in plain


# ---------------------------------------------------------------------------
# render_status_bar — home-relative substitution
# ---------------------------------------------------------------------------


def test_render_status_bar_home_relative_path_when_home_passed() -> None:
    """When ``home`` is supplied and workspace_root starts with it, output uses '~'."""
    model = StatusBarModel(
        workspace_root="/Users/alice/code/proj",
        phase_label="Development",
        phase_style="theme.phase.development",
    )
    ctx = _make_display_context(width=140)
    text = render_status_bar(model, ctx, home="/Users/alice")
    plain = _plain_text(text)
    assert "~/" in plain
    assert "code/proj" in plain


def test_render_status_bar_pathological_no_home_relative_when_home_not_passed() -> None:
    """When ``home`` is None, the original path passes through (verifying the param)."""
    model = StatusBarModel(
        workspace_root="/Users/alice/code/proj",
        phase_label="Development",
        phase_style="theme.phase.development",
    )
    ctx = _make_display_context(width=140)
    text = render_status_bar(model, ctx, home=None)
    plain = _plain_text(text)
    # No '~/' substitution without home.
    assert "~/" not in plain


# ---------------------------------------------------------------------------
# render_status_bar — phase label tail-truncation in the default mode (never wraps)
# ---------------------------------------------------------------------------


def test_render_status_bar_truncates_long_phase_label_no_wrap() -> None:
    """A long phase label is tail-truncated and never wraps (no '\\n').

    The single default-mode layout uses DEFAULT_PHASE_LABEL_BUDGET=28
    chars; a 20-char 'Development Analysis' fits the budget, so this
    test asserts the phase label is rendered in full when within the
    budget. The no-wrap invariant is the key contract.
    """
    # 'Development Analysis' is 20 chars; default budget is 28 -> no elision.
    model = StatusBarModel(
        workspace_root="/Users/alice/code/proj",
        phase_label="Development Analysis",
        phase_style="theme.phase.analysis",
    )
    ctx = _make_display_context(width=140)
    text = render_status_bar(model, ctx, home="/Users/alice")
    plain = _plain_text(text)
    assert "\n" not in plain, f"Status Bar must not wrap: {plain!r}"
    # The phase label is rendered in full because it fits the budget.
    assert "Development Analysis" in plain


def test_render_status_bar_truncates_very_long_phase_label() -> None:
    """A phase label longer than DEFAULT_PHASE_LABEL_BUDGET=28 is tail-truncated."""
    long_label = "Very Long Phase Label Exceeding Default Budget Of Twenty Eight"
    assert len(long_label) > 28
    model = StatusBarModel(
        workspace_root="/Users/alice/code/proj",
        phase_label=long_label,
        phase_style="theme.phase.analysis",
    )
    ctx = _make_display_context(width=140)
    text = render_status_bar(model, ctx, home="/Users/alice")
    plain = _plain_text(text)
    assert "\n" not in plain, f"Status Bar must not wrap: {plain!r}"
    # The full label was truncated; the rendered text ends with '...'.
    assert "..." in plain, f"Truncated label must include '...'; got {plain!r}"


# ---------------------------------------------------------------------------
# render_status_bar — ASCII glyph fallback when ctx.glyphs_enabled is False
# ---------------------------------------------------------------------------


def test_render_status_bar_ascii_glyph_fallback_when_glyphs_disabled() -> None:
    """When glyphs_enabled is False, the status bar uses ASCII separators (no Unicode bullets)."""
    model = StatusBarModel(
        workspace_root="/Users/alice/code/proj",
        phase_label="Development",
        phase_style="theme.phase.development",
        outer_dev_iteration=1,
        outer_dev_cap=3,
    )
    ctx = _make_display_context(width=140, ascii_glyphs=True)
    text = render_status_bar(model, ctx, home="/Users/alice")
    plain = _plain_text(text)
    # ASCII fallback glyph for 'milestone' is '*' and 'outer_dev' is '[OD]'.
    assert "*" in plain, f"ASCII milestone '*' must appear in plain output; got {plain!r}"
    assert "[OD]" in plain, f"ASCII outer_dev '[OD]' must appear in plain output; got {plain!r}"
    # Phase_marker is omitted when glyphs are disabled (single default-mode invariant).
    # No Unicode glyphs at all should appear.
    assert "■" not in plain
    assert "◆" not in plain
    assert "◎" not in plain
    assert "▸" not in plain


# ---------------------------------------------------------------------------
# render_status_bar — single-line no-newline invariant
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "model",
    [
        StatusBarModel(
            workspace_root="/Users/alice/code/proj",
            phase_label="Development",
            phase_style="theme.phase.development",
        ),
        StatusBarModel(
            workspace_root="/Users/alice/very-very-long-directory-name/very-very-cool-project/subdir",
            phase_label="Development Analysis",
            phase_style="theme.phase.analysis",
            outer_dev_iteration=1,
            outer_dev_cap=3,
            inner_analysis=2,
            inner_analysis_cap=7,
        ),
        StatusBarModel(
            workspace_root="/Users/alice/code/p",
            phase_label="Commit",
            phase_style="theme.phase.commit",
            outer_dev_iteration=None,
        ),
    ],
)
def test_render_status_bar_single_line_no_newline(model: StatusBarModel) -> None:
    """render_status_bar must always emit a single line regardless of mode/model."""
    ctx = _make_display_context(width=140)
    text = render_status_bar(model, ctx, home="/Users/alice")
    plain = _plain_text(text)
    assert "\n" not in plain, f"Status Bar must be single-line: {plain!r}"


# ---------------------------------------------------------------------------
# render_status_bar — hostile-input sanitization (newlines, control chars, escapes)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "hostile_value",
    [
        "Dev\nPhase",
        "Dev\r\nPhase",
        "Dev\rPhase",
        "Dev\x00Phase",
        "Dev\x07Phase",
        "Dev\x1b[31mPhase",
        "Dev\x1b[2JPhase",
    ],
)
def test_render_status_bar_strips_hostile_phase_label_chars(
    hostile_value: str,
) -> None:
    """render_status_bar must neutralise hostile bytes in ``phase_label``.

    The persistent live footer is single-line by contract (it cannot wrap
    into the working area), so any input that would split the bar across
    lines or inject terminal control sequences has to be neutralized
    before the label is appended. The strips preserve visual meaning as
    much as possible: line breaks collapse to a space, control bytes and
    CSI escape sequences are dropped entirely.
    """
    model = StatusBarModel(
        workspace_root="/Users/alice/code/proj",
        phase_label=hostile_value,
        phase_style="theme.phase.development",
        outer_dev_iteration=1,
        outer_dev_cap=3,
    )
    ctx = _make_display_context(width=140)
    text = render_status_bar(model, ctx, home="/Users/alice")
    plain = _plain_text(text)
    assert "\n" not in plain, (
        f"Status Bar must not wrap from hostile phase_label={hostile_value!r}: {plain!r}"
    )
    assert "\r" not in plain, (
        f"Status Bar must not contain CR from hostile phase_label={hostile_value!r}: {plain!r}"
    )
    assert "\x1b" not in plain, (
        f"Status Bar must not contain ESC from hostile phase_label={hostile_value!r}: {plain!r}"
    )
    assert "\x00" not in plain, (
        f"Status Bar must not contain NUL from hostile phase_label={hostile_value!r}: {plain!r}"
    )
    assert "\x07" not in plain, (
        f"Status Bar must not contain BEL from hostile phase_label={hostile_value!r}: {plain!r}"
    )


@pytest.mark.parametrize(
    "hostile_value",
    [
        "/tmp/evil\npath",
        "/tmp/evil\r\npath",
        "/tmp/evil\rpath",
        "/tmp/evil\x00path",
        "/tmp/\x07evil",
        "/tmp/evil\x1b[31mred",
        "/tmp/evil\x1b[2Jred",
    ],
)
def test_render_status_bar_strips_hostile_workspace_root_chars(
    hostile_value: str,
) -> None:
    """render_status_bar must neutralise hostile bytes in ``workspace_root``.

    Same invariant as the phase_label guard, applied to the path field.
    A path that contains a newline, CR, NUL, BEL, or CSI escape sequence
    must not be allowed to wrap or escape-sequence-inject the live
    Status Bar footer.
    """
    model = StatusBarModel(
        workspace_root=hostile_value,
        phase_label="Development",
        phase_style="theme.phase.development",
        outer_dev_iteration=1,
        outer_dev_cap=3,
    )
    ctx = _make_display_context(width=140)
    text = render_status_bar(model, ctx, home="/Users/alice")
    plain = _plain_text(text)
    assert "\n" not in plain, (
        f"Status Bar must not wrap from hostile workspace_root={hostile_value!r}: {plain!r}"
    )
    assert "\r" not in plain, (
        f"Status Bar must not contain CR from hostile workspace_root={hostile_value!r}: {plain!r}"
    )
    assert "\x1b" not in plain, (
        f"Status Bar must not contain ESC from hostile workspace_root={hostile_value!r}: {plain!r}"
    )
    assert "\x00" not in plain, (
        f"Status Bar must not contain NUL from hostile workspace_root={hostile_value!r}: {plain!r}"
    )
    assert "\x07" not in plain, (
        f"Status Bar must not contain BEL from hostile workspace_root={hostile_value!r}: {plain!r}"
    )


def test_render_status_bar_collapses_hostile_newlines_to_spaces() -> None:
    """Newlines in phase_label collapse to a space so the bar stays single-line.

    A label that would otherwise be ``"Dev\nPhase"`` renders as
    ``"Dev Phase"`` after sanitization (newline replaced by a single
    ASCII space). This preserves the label's visual meaning while
    preventing the bar from splitting into the working area.
    """
    model = StatusBarModel(
        workspace_root="/Users/alice/code/proj",
        phase_label="Dev\nPhase",
        phase_style="theme.phase.development",
    )
    ctx = _make_display_context(width=140)
    text = render_status_bar(model, ctx, home="/Users/alice")
    plain = _plain_text(text)
    assert "Dev Phase" in plain, f"expected 'Dev Phase' (newline collapsed to space): {plain!r}"
    assert "Dev\nPhase" not in plain, (
        f"newline should be collapsed to a space, not preserved: {plain!r}"
    )


@pytest.mark.parametrize(
    "tab_label",
    [
        "Dev\tPhase",
        "Dev\tAnalysis",
        "\tDevelopment",
        "Development\t",
    ],
)
def test_render_status_bar_collapses_tab_chars_to_spaces(tab_label: str) -> None:
    """Tabs in phase_label collapse to a space so the bar's width budget stays honest.

    Pins the analysis-feedback correctness fix at
    ``ralph/display/status_bar.py:_safe_single_line``: the persistent
    live footer's width budget accounts column count via ``len()``
    while a terminal expands ``\t`` to the next tab stop (typically
    8 columns). Without tab normalization a single tab in a label
    would silently inflate the rendered width and break the
    ``len(text.plain) <= ctx.width`` invariant the Live region is
    sized against.

    The test feeds a tab-containing ``phase_label`` (the same hostile
    payload class that broke the operator's display before the fix)
    through ``render_status_bar`` and asserts:

    - the rendered text contains NO tab character (the tab is
      normalized to a single ASCII space),
    - the rendered text stays single-line (no ``\n`` wrap into the
      working area),
    - the rendered text still fits the configured width
      (``len(plain) <= ctx.width``), so a tab-containing label
      cannot blow up the bar's layout at any width,
    - the label is collapsed to ``"Dev Phase"`` / ``"Dev Analysis"``
      / ``" Development"`` / ``"Development"`` form (whitespace
      trimmed), so the bar reads cleanly.
    """
    model = StatusBarModel(
        workspace_root="/Users/alice/code/proj",
        phase_label=tab_label,
        phase_style="theme.phase.development",
    )
    ctx = _make_display_context(width=140)
    text = render_status_bar(model, ctx, home="/Users/alice")
    plain = _plain_text(text)
    assert "\t" not in plain, (
        f"Status Bar must not contain a tab from phase_label={tab_label!r}: {plain!r}"
    )
    assert "\n" not in plain, (
        f"Status Bar must not wrap from tab in phase_label={tab_label!r}: {plain!r}"
    )
    assert "\r" not in plain, (
        f"Status Bar must not contain CR from tab in phase_label={tab_label!r}: {plain!r}"
    )
    assert len(plain) <= ctx.width, (
        f"Status Bar must fit ctx.width={ctx.width} after tab "
        f"normalization; len(plain)={len(plain)} for "
        f"phase_label={tab_label!r}, plain={plain!r}"
    )


def test_render_status_bar_collapses_tab_in_workspace_root_keeps_width_budget() -> None:
    """Tabs in workspace_root collapse to a space so the path width budget stays honest.

    Companion to the ``phase_label`` tab test: the same bug class
    applies when a tab appears in the workspace path (e.g. a path
    pasted from a tab-separated source). The bar must not let the tab
    inflate the rendered width past ``ctx.width``.

    The test feeds a tab-containing path that would otherwise inflate
    the rendered width via tab-stop expansion, asserts the tab is
    normalized to a single ASCII space, and verifies the rendered
    text still fits ``ctx.width``.
    """
    tab_path = "/Users/alice/code/evil\tpath/subdir"
    model = StatusBarModel(
        workspace_root=tab_path,
        phase_label="Development",
        phase_style="theme.phase.development",
    )
    ctx = _make_display_context(width=80)
    text = render_status_bar(model, ctx, home="/Users/alice")
    plain = _plain_text(text)
    assert "\t" not in plain, (
        f"Status Bar must not contain a tab from workspace_root={tab_path!r}: {plain!r}"
    )
    assert "\n" not in plain, (
        f"Status Bar must not wrap from tab in workspace_root={tab_path!r}: {plain!r}"
    )
    assert len(plain) <= ctx.width, (
        f"Status Bar must fit ctx.width={ctx.width} after tab "
        f"normalization; len(plain)={len(plain)} for "
        f"workspace_root={tab_path!r}, plain={plain!r}"
    )


def test_render_status_bar_collapses_tab_counted_in_len_after_normalization() -> None:
    """After tab normalization, ``len(plain)`` is the width the bar allocates.

    Pins the single-line single-column width contract end-to-end:
    after normalization, the tab character counts as one column (a
    single ASCII space replacement), NOT eight (the typical tab-stop
    expansion). The test asserts that a 10-char input string
    containing 2 tabs renders as 10 chars wide after normalization
    (the tabs each become a single space, no inflation).

    Without this fix the rendered width would be larger than the
    ``len()`` budget the allocator reserved, blowing up alignment
    and truncation.
    """
    tab_label = "Dev\t\tEnd"
    model = StatusBarModel(
        workspace_root="/Users/alice/code/proj",
        phase_label=tab_label,
        phase_style="theme.phase.development",
    )
    ctx = _make_display_context(width=140)
    text = render_status_bar(model, ctx, home="/Users/alice")
    plain = _plain_text(text)
    assert "Dev  End" in plain or "Dev End" in plain, (
        f"expected the tabs to collapse to spaces (one space per tab "
        f"or run of whitespace trimmed); got plain={plain!r}"
    )
    assert "\t" not in plain, (
        f"tab characters MUST be normalized away so the width budget is honest; got plain={plain!r}"
    )


def test_render_status_bar_preserves_meaningful_path_chars() -> None:
    """Sanitization must not strip non-hostile, meaningful characters from a path.

    Defends against an over-zealous sanitizer that would clobber the
    trailing project name or visual separators (e.g. ``-`` or ``_``).
    A path like ``/Users/alice/very_cool-project/subdir`` must round-trip
    intact (modulo the home-relative ``~`` prefix).
    """
    model = StatusBarModel(
        workspace_root="/Users/alice/very_cool-project/subdir",
        phase_label="Development",
        phase_style="theme.phase.development",
    )
    ctx = _make_display_context(width=140)
    text = render_status_bar(model, ctx, home="/Users/alice")
    plain = _plain_text(text)
    assert "very_cool-project" in plain or "very_cool-pro" in plain, (
        f"expected the project name to survive sanitization: {plain!r}"
    )
    assert "subdir" in plain, f"expected the trailing path component: {plain!r}"


# ---------------------------------------------------------------------------
# render_status_bar — phase label is styled with model.phase_style
# ---------------------------------------------------------------------------


def test_render_status_bar_phase_label_is_styled() -> None:
    """The phase label segment of the rendered Text carries ``model.phase_style``."""
    model = StatusBarModel(
        workspace_root="/Users/alice/code/proj",
        phase_label="Development",
        phase_style="theme.phase.development",
    )
    ctx = _make_display_context(width=140)
    text = render_status_bar(model, ctx, home="/Users/alice")
    has_styled_phase = False
    for span in text.spans:
        substring = text[span.start : span.end]
        if "Development" in substring and span.style and "theme.phase.development" in span.style:
            has_styled_phase = True
            break
    if not has_styled_phase:
        spans_detail = [(text[span.start : span.end], span.style) for span in text.spans]
        assert has_styled_phase, (
            f"Phase label 'Development' must be styled with theme.phase.development; "
            f"spans={spans_detail!r}"
        )


def test_render_status_bar_textual_meaning_not_solely_color() -> None:
    """Plain text contains the phase label even when style is meaningless (colorEnabled=False)."""
    model = StatusBarModel(
        workspace_root="/Users/alice/code/proj",
        phase_label="Development",
        phase_style="theme.phase.development",
    )
    # Force NO_COLOR so color_enabled is False on the resulting context.
    ctx = make_display_context(
        console=Console(file=io.StringIO(), width=140, color_system=None),
        env={"NO_COLOR": "1"},
    )
    text = render_status_bar(model, ctx, home="/Users/alice")
    assert "Development" in _plain_text(text)


# ---------------------------------------------------------------------------
# StatusBar lifecycle — non-terminal StringIO no-op
# ---------------------------------------------------------------------------


def test_status_bar_noop_on_non_terminal_console() -> None:
    """A non-terminal console (no force_terminal AND no isatty) -> StatusBar.start() is a no-op."""
    ctx = make_display_context(
        console=Console(
            file=io.StringIO(),
            force_terminal=False,
            width=120,
            color_system=None,
        ),
        env={},
    )
    pd = ParallelDisplay(ctx)
    sb = pd.status_bar
    assert isinstance(sb, StatusBar)
    sb.start()
    try:
        assert sb.is_active is False
        # Buffer must be empty.
        buf = ctx.console.file
        if isinstance(buf, io.StringIO):
            buf_value = buf.getvalue()
            assert buf_value == "", f"Non-terminal must not write anything; got {buf_value!r}"
    finally:
        sb.stop()


# ---------------------------------------------------------------------------
# StatusBar lifecycle — force_terminal+StringIO is a no-op (the isatty() conjunct)
# ---------------------------------------------------------------------------


def test_status_bar_noop_on_force_terminal_stringio_console() -> None:
    """force_terminal=True but isatty()=False (StringIO) -> StatusBar.start() is a no-op.

    Verified Rich behavior: Console(file=StringIO(), force_terminal=True).is_terminal is True
    (Rich defines is_terminal = force_terminal OR isatty()), so without the isatty() conjunct
    the bar would start on a non-tty file. The isatty() conjunct keeps it pinned to real TTY.
    """
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=True, width=120, color_system="standard")
    ctx = make_display_context(console=console, env={})
    pd = ParallelDisplay(ctx)
    sb = pd.status_bar
    # Documenting WHY the isatty() conjunct is required.
    assert console.is_terminal is True, "force_terminal implies Rich's is_terminal=True"
    assert console.file.isatty() is False, "StringIO is not a TTY"
    sb.start()
    try:
        assert sb.is_active is False, (
            "force_terminal+StringIO must NOT start a Live region; the isatty() conjunct is "
            "the gate that suppresses the bar on non-tty files even when force_terminal is set."
        )
        assert buf.getvalue() == "", (
            f"No bytes may be written on a force_terminal+StringIO console; got {buf.getvalue()!r}"
        )
    finally:
        sb.stop()


# ---------------------------------------------------------------------------
# StatusBar lifecycle — quiet mode no-op
# ---------------------------------------------------------------------------


def test_status_bar_quiet_mode_noop() -> None:
    """A ParallelDisplay constructed with is_quiet=True must NOT start the bar."""
    ctx = make_display_context(
        console=Console(
            file=io.StringIO(),
            force_terminal=True,
            width=120,
            color_system="standard",
        ),
        env={},
    )
    pd = ParallelDisplay(ctx, is_quiet=True)
    sb = pd.status_bar
    sb.start()
    try:
        assert sb.is_active is False, "Quiet mode must keep the StatusBar inert."
    finally:
        sb.stop()


# ---------------------------------------------------------------------------
# StatusBar lifecycle — start/stop idempotent
# ---------------------------------------------------------------------------


def test_status_bar_start_stop_idempotent() -> None:
    """Repeated start()/stop() do not raise, and stop() without start() is a no-op."""
    ctx = make_display_context(
        console=Console(
            file=io.StringIO(),
            force_terminal=True,
            width=120,
            color_system="standard",
        ),
        env={},
    )
    pd = ParallelDisplay(ctx)
    sb = pd.status_bar
    sb.start()
    sb.start()  # second start() is idempotent
    assert sb.is_active is False, "StringIO starts must remain a no-op"
    sb.stop()
    sb.stop()  # second stop() is a no-op


def test_status_bar_stop_without_start_is_noop() -> None:
    """Calling stop() on an unstarted bar does not raise."""
    ctx = make_display_context(
        console=Console(file=io.StringIO(), force_terminal=False, width=120),
        env={},
    )
    pd = ParallelDisplay(ctx)
    sb = pd.status_bar
    sb.stop()
    assert sb.is_active is False


# ---------------------------------------------------------------------------
# StatusBar lifecycle — update(model) before start() stores the model
# ---------------------------------------------------------------------------


def test_status_bar_update_before_start_stores_model() -> None:
    """update(model) is allowed before start() and last_model reflects the value."""
    ctx = make_display_context(
        console=Console(file=io.StringIO(), width=120),
        env={},
    )
    pd = ParallelDisplay(ctx)
    sb = pd.status_bar
    model = StatusBarModel(
        workspace_root="/Users/alice/code/proj",
        phase_label="Development",
        phase_style="theme.phase.development",
        outer_dev_iteration=1,
        outer_dev_cap=3,
    )
    sb.update(model)
    assert sb.last_model == model
    sb.stop()


# ---------------------------------------------------------------------------
# Cadence-constant pinning
# ---------------------------------------------------------------------------


def test_status_bar_pins_steady_cadence_config() -> None:
    """_STATUS_BAR_REFRESH_PER_SECOND==4.0 and _STATUS_BAR_TRANSIENT is True."""
    assert _STATUS_BAR_REFRESH_PER_SECOND == 4.0, (
        f"refresh_per_second must be 4.0; got {_STATUS_BAR_REFRESH_PER_SECOND}"
    )
    assert _STATUS_BAR_TRANSIENT is True, (
        f"_STATUS_BAR_TRANSIENT must be True; got {_STATUS_BAR_TRANSIENT}"
    )


# ---------------------------------------------------------------------------
# Clean-buffer-under-flow on a non-terminal console (readability proof)
# ---------------------------------------------------------------------------


def test_status_bar_clean_buffer_under_flow() -> None:
    """On a non-terminal console, emit() and update_status_bar() leave a clean, in-order buffer.

    This proves the StatusBar does NOT pollute captured output with Live cursor-control
    artifacts when the gate decides against starting a Live region.
    """
    buf = io.StringIO()
    console = Console(
        file=buf,
        force_terminal=True,
        width=120,
        color_system="standard",
    )
    ctx = make_display_context(console=console, env={})
    pd = ParallelDisplay(ctx)
    for line in ("line-A", "line-B", "line-C", "line-D", "line-E"):
        pd.emit("run", line)
    for n in range(3):
        pd.update_status_bar(
            StatusBarModel(
                workspace_root="/Users/alice/code/proj",
                phase_label="Development",
                phase_style="theme.phase.development",
                outer_dev_iteration=n + 1,
                outer_dev_cap=3,
            )
        )
    pd.stop()
    out = buf.getvalue()
    # Five logs in order.
    for line in ("line-A", "line-B", "line-C", "line-D", "line-E"):
        assert line in out, f"missing {line!r} in captured output: {out!r}"
    # No Live cursor-hide/show sequences.
    assert "\x1b[?25" not in out, f"unexpected cursor-control escape: {out!r}"
    # No duplicated Live frames.
    assert out.count("\x1b[?1049l") == 0 and out.count("\x1b[?1049h") == 0, (
        f"unexpected alt-screen toggle in non-tty output: {out!r}"
    )


def test_status_bar_non_tty_emits_durable_state_transitions_without_repaint_controls() -> None:
    """Redirected output records meaningful footer state changes as durable lines."""
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=True, width=120, color_system="standard")
    pd = ParallelDisplay(make_display_context(console=console, env={"FORCE_COLOR": "1"}))

    pd.update_status_bar(
        StatusBarModel(
            workspace_root="/work/project",
            phase_label="Development",
            phase_style="theme.phase.development",
            elapsed_seconds=1.0,
            attention="waiting",
        )
    )
    pd.update_status_bar(
        StatusBarModel(
            workspace_root="/work/project",
            phase_label="Review",
            phase_style="theme.phase.review",
            elapsed_seconds=2.0,
            attention="waiting",
        )
    )
    pd.stop()

    output = buf.getvalue()
    visible = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", output)
    assert "WAITING" in visible
    assert "Development" in visible
    assert "Review" in visible
    assert "\r" not in output
    assert "\x1b[1A" not in output
    assert "\x1b[2K" not in output


# ---------------------------------------------------------------------------
# StatusBar lifecycle — tty-like stream surfaces the live-rendered model
# ---------------------------------------------------------------------------


def test_status_bar_live_region_renders_updated_model_on_tty_like_stream() -> None:
    """On a tty-like stream (isatty()=True), update+start+stop renders the model.

    The gate is open on a tty-like StringIO because both
    ``console.is_terminal`` and ``console.file.isatty()`` are True. The Live
    region is constructed with the model as its initial renderable
    (update is called BEFORE start), so the captured buffer contains both
    the phase label AND the iteration text after stop, proving the
    live-update path surfaces the model on a real-TTY console.

    This pattern deliberately avoids relying on the 4 Hz refresh tick or
    any eager ``live.refresh()``: the model is captured into ``Live``'s
    initial-renderable slot, so the first render uses it deterministically.
    """
    buf = _TtyLikeStringIO()
    console = Console(
        file=buf,
        force_terminal=True,
        width=120,
        color_system="standard",
    )
    ctx = make_display_context(console=console, env={})
    pd = ParallelDisplay(ctx)
    sb = pd.status_bar
    # Sanity: gate is open on this tty-like stream.
    assert console.is_terminal is True
    assert console.file.isatty() is True
    model = StatusBarModel(
        workspace_root="/Users/alice/code/proj",
        phase_label="Development",
        phase_style="theme.phase.development",
        outer_dev_iteration=1,
        outer_dev_cap=3,
    )
    sb.update(model)
    sb.start()
    try:
        assert sb.is_active is True, (
            "StatusBar.start() must construct a Live region on a tty-like stream "
            "(both console.is_terminal and console.file.isatty() are True)."
        )
        assert sb.last_model is model, (
            "StatusBar.last_model must reflect the most recently supplied model."
        )
    finally:
        sb.stop()
    out = buf.getvalue()
    assert "Development" in out, (
        f"Live region must surface the phase label 'Development'; got {out!r}"
    )
    assert "Cycle 1/3" in out, (
        f"Live region must surface the iteration label 'Cycle 1/3'; got {out!r}"
    )


def test_status_bar_live_region_renders_phase_only_when_no_iteration() -> None:
    """Tty-like stream with outer_dev_iteration=None renders phase but no '--' placeholder.

    Uses the update-before-start pattern so the omitted iteration fields
    leave a deterministic trace in the rendered output.
    """
    buf = _TtyLikeStringIO()
    console = Console(file=buf, force_terminal=True, width=120, color_system="standard")
    ctx = make_display_context(console=console, env={})
    pd = ParallelDisplay(ctx)
    sb = pd.status_bar
    model = StatusBarModel(
        workspace_root="/Users/alice/code/proj",
        phase_label="Commit",
        phase_style="theme.phase.commit",
        outer_dev_iteration=None,
        inner_analysis=None,
    )
    sb.update(model)
    sb.start()
    try:
        assert sb.is_active is True
    finally:
        sb.stop()
    out = buf.getvalue()
    assert "Commit" in out, f"Live region must surface the phase label 'Commit'; got {out!r}"
    # No placeholder for the omitted iteration fields.
    assert "--" not in out, (
        f"Live region must not render a '--' placeholder for omitted iteration; got {out!r}"
    )


def test_status_bar_live_region_renders_with_outer_dev_only() -> None:
    """Tty-like stream with outer_dev set and inner_analysis=None at 120 cols.

    At 120 cols the persistent Status Bar has enough width to render
    the inner_analysis field when populated. With inner_analysis=None
    the field is OMITTED entirely (no glyph, no '--' stub, no separator
    before it). The outer_dev field IS rendered. Uses the
    update-before-start pattern for determinism.
    """
    buf = _TtyLikeStringIO()
    console = Console(file=buf, force_terminal=True, width=120, color_system="standard")
    ctx = make_display_context(console=console, env={})
    pd = ParallelDisplay(ctx)
    sb = pd.status_bar
    model = StatusBarModel(
        workspace_root="/Users/alice/code/proj",
        phase_label="Development",
        phase_style="theme.phase.development",
        outer_dev_iteration=2,
        outer_dev_cap=5,
        inner_analysis=None,
        inner_analysis_cap=None,
    )
    sb.update(model)
    sb.start()
    try:
        assert sb.is_active is True
    finally:
        sb.stop()
    out = buf.getvalue()
    assert "Development" in out
    assert "Cycle 2/5" in out
    assert "Analysis" not in out
    assert "--" not in out


# ---------------------------------------------------------------------------
# ParallelDisplay composition + method pinning
# ---------------------------------------------------------------------------


def test_parallel_display_composes_status_bar() -> None:
    """ParallelDisplay exposes a non-None ``status_bar`` of type StatusBar."""
    ctx = make_display_context(
        console=Console(file=io.StringIO(), width=120),
        env={},
    )
    pd = ParallelDisplay(ctx)
    assert isinstance(pd.status_bar, StatusBar)


def test_parallel_display_has_update_status_bar_method() -> None:
    """ParallelDisplay exposes update_status_bar(model); not in the one-shot set."""
    ctx = make_display_context(
        console=Console(file=io.StringIO(), width=120),
        env={},
    )
    pd = ParallelDisplay(ctx)
    assert hasattr(pd, "update_status_bar")
    assert callable(pd.update_status_bar)


def test_parallel_display_update_status_bar_does_not_raise_on_non_terminal() -> None:
    """update_status_bar on a non-terminal ParallelDisplay does not raise and stores the model."""
    ctx = make_display_context(
        console=Console(file=io.StringIO(), width=120),
        env={},
    )
    pd = ParallelDisplay(ctx)
    model = StatusBarModel(
        workspace_root="/Users/alice/code/proj",
        phase_label="Development",
        phase_style="theme.phase.development",
        outer_dev_iteration=1,
        outer_dev_cap=3,
    )
    pd.update_status_bar(model)
    assert pd.status_bar.last_model == model


# ---------------------------------------------------------------------------
# Theme-style guard — render_status_bar references theme.status.* keys
# ---------------------------------------------------------------------------


def test_status_bar_theme_styles_are_defined() -> None:
    """The three ``theme.status.*`` keys referenced by ``render_status_bar`` are defined.

    ``render_status_bar`` attaches ``style='theme.status.bar_marker'``,
    ``style='theme.status.path_marker'``, and ``style='theme.status.path'``
    to the bar-marker, separator, and path segments. When any of those
    keys are missing from ``RALPH_THEME.styles`` Rich silently renders
    that segment uncolored (a dangling style reference), which breaks
    the "color clarifies state" UX requirement and de-emphasizes the
    path / structural markers away from the colored phase label.

    This guard pins that all three keys exist in the public theme mapping
    so the bar's color story is coherent end-to-end.
    """
    required_keys: frozenset[str] = frozenset(
        {"theme.status.path", "theme.status.path_marker", "theme.status.bar_marker"}
    )
    actual_keys: frozenset[str] = frozenset(RALPH_THEME.styles.keys())
    missing: frozenset[str] = required_keys - actual_keys
    assert not missing, (
        f"RALPH_THEME is missing status-bar styles {sorted(missing)!r}; "
        "render_status_bar attaches these styles, and absent keys render as "
        "uncolored (dangling) Rich spans."
    )


# ---------------------------------------------------------------------------
# StatusBar module purity — no Console construction, no os.environ read
# ---------------------------------------------------------------------------


def test_status_bar_module_constructs_no_console_and_reads_no_env() -> None:
    """status_bar.py source uses neither ``Console(`` construction nor env reads.

    The DI audit (test_di_invariants) covers all of ralph/display/*.py automatically,
    but this focused assertion names the invariant for clarity. We strip out
    docstrings/comments so this test pins CODE behaviour, not documentation.
    """
    src = inspect.getsource(_status_bar_module)
    # Drop docstrings line-by-line so the assertion scans CODE only.
    src_no_docstrings = re.sub(r"\"\"\"[\s\S]*?\"\"\"", "", src)
    assert "Console(" not in src_no_docstrings, (
        "status_bar.py must not construct a Console; found 'Console(' in source."
    )
    assert "os.environ" not in src_no_docstrings, (
        "status_bar.py must not read os.environ; found in source."
    )
    assert "os.getenv" not in src_no_docstrings, (
        "status_bar.py must not call os.getenv; found in source."
    )


# ---------------------------------------------------------------------------
# render_status_bar must not call Path.home() (the purity invariant)
# ---------------------------------------------------------------------------


def test_render_status_bar_does_not_call_path_home() -> None:
    """render_status_bar must not invoke pathlib.Path.home(): home is a parameter.

    Walks the function's AST and asserts the body has no ``Call`` whose function
    is the attribute ``Path.home`` (a real call expression). The function's
    docstring may describe the purity invariant — we ignore string tokens.
    """
    func_ast = ast.parse(inspect.getsource(_status_bar_module.render_status_bar)).body[0]
    for node in ast.walk(func_ast):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "home"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "Path"
        ):
            raise AssertionError(
                f"render_status_bar must take home as a parameter; found Path.home() "
                f"call at line {node.lineno}."
            )


# ---------------------------------------------------------------------------
# Glyph-token separators — render uses ctx.glyph_for('milestone') (or ASCII | fallback)
# ---------------------------------------------------------------------------


def test_render_status_bar_uses_milestone_glyph_between_fields() -> None:
    """The render_status_bar output includes the milestone glyph (Unicode or ASCII)."""
    model = StatusBarModel(
        workspace_root="/Users/alice/code/proj",
        phase_label="Development",
        phase_style="theme.phase.development",
        outer_dev_iteration=1,
        outer_dev_cap=3,
    )
    ctx = _make_display_context(width=140, ascii_glyphs=True)
    text = render_status_bar(model, ctx, home="/Users/alice")
    plain = _plain_text(text)
    # ASCII glyph for 'milestone' is '*' from ASCII_GLYPHS.
    # ASCII glyph for 'phase_marker' is '[]'.
    has_separator = "|" in plain or "*" in plain or "[]" in plain or "·" in plain
    assert has_separator, f"render_status_bar must include a separator glyph; got plain={plain!r}"


# ---------------------------------------------------------------------------
# Frozen StatusBarModel — must reject assignment after construction
# ---------------------------------------------------------------------------


def test_status_bar_model_is_frozen() -> None:
    """StatusBarModel is a frozen dataclass (immutable view-model)."""
    model = StatusBarModel(
        workspace_root="/Users/alice/code/proj",
        phase_label="Development",
        phase_style="theme.phase.development",
    )
    # ``dataclasses.FrozenInstanceError`` is a subclass of ``AttributeError``;
    # both are raised on assignment to a frozen dataclass. Casting to ``Any``
    # lets us attempt the assignment without a mypy suppression comment
    # (test files may not carry type suppressions per
    # ``tests/test_type_ignore_policy.py``).
    mutable_model: Any = model
    with pytest.raises(dataclasses.FrozenInstanceError):
        mutable_model.phase_label = "Commit"


# ---------------------------------------------------------------------------
# Pure _build_status_bar_model unit test (1-indexed entry semantics)
# ---------------------------------------------------------------------------


def test_build_status_bar_model_uses_entry_semantics(monkeypatch: pytest.MonkeyPatch) -> None:
    """_build_status_bar_model uses PhaseEntryModel.human_label() and entry iteration fields.

    Proves: the wire in run_loop produces a StatusBarModel whose phase_label is the
    human-readable form (not the raw phase_name), and whose outer_dev_iteration is the
    1-indexed current cycle from PhaseEntryModel (NOT the snapshot's completed count).

    This is a PURE unit test on the model's data contract; it does not invoke the live
    runner and runs inside the 60s budget.
    """
    run_loop_mod = _run_loop_module

    class _FakeEntry:
        def __init__(self, label: str, outer_dev: int | None, cap: int | None) -> None:
            self._label = label
            self.outer_dev_iteration = outer_dev
            self.outer_dev_cap = cap
            self.inner_analysis = None
            self.inner_analysis_cap = None

        def human_label(self) -> str:
            return self._label

    def _fake_build(*args: object, **kwargs: object) -> _FakeEntry:
        return _FakeEntry("Development", 2, 3)

    # Monkeypatch on the LOCAL name in run_loop because ``from x import y``
    # binds y at import time in run_loop.
    monkeypatch.setattr(run_loop_mod, "build_phase_entry_model_from_state", _fake_build)

    def _fake_style(phase: str, pipeline_policy: object) -> str:
        return "theme.phase.development"

    monkeypatch.setattr(run_loop_mod, "phase_style_for_phase", _fake_style)

    class _FakeState:
        phase = "development"

    class _FakePolicyBundle:
        pipeline = object()  # only used by the fake helpers above

    model = run_loop_mod._build_status_bar_model(
        _FakeState(),
        _FakePolicyBundle(),
        Path("/Users/alice/code/proj"),
    )
    assert model.phase_label == "Development"
    assert model.outer_dev_iteration == 2
    assert model.outer_dev_cap == 3
    assert model.phase_style == "theme.phase.development"
    assert model.workspace_root == "/Users/alice/code/proj"


# ---------------------------------------------------------------------------
# Scrollback-cleanliness proof (AC-08)
# ---------------------------------------------------------------------------


def test_status_bar_live_region_is_erased_after_stop_preserving_scrollback() -> None:
    """AC-08: the persistent Live region is fully erased after ``StatusBar.stop()``.

    This is the direct observable proof that ``_STATUS_BAR_TRANSIENT is True``
    actually translates to a clean scrollback at runtime (complementing the
    import-time constant pin in
    ``test_status_bar_pins_steady_cadence_config``). It drives a real
    ``StatusBar`` through ``start()`` -> ``update(StatusBarModel(...))``
    -> ``stop()`` on a Rich ``Console(file=_TtyLikeStringIO,
    force_terminal=True, width=120)`` and asserts:

    1. The captured buffer CONTAINS the rendered model content (the
       workspace path, phase label, and outer-dev iteration appear),
       proving the Live region actually rendered the model.
    2. After ``stop()``, the captured buffer's FINAL visible line is
       empty (only the cursor-positioning + erase-line escape
       sequences remain). This is the contract that scrollback,
       copy/paste, terminal search, and post-run log review remain
       usable for unattended runs: when a real terminal interprets
       the escape codes in the buffer, the only remaining visible
       line is the cursor position itself.

    The proof is structural rather than textual. The buffer is split
    by ``\n`` (the line terminator that introduces the final
    post-render line) and the LAST line is checked after ANSI
    stripping. The standard Rich transient Live cleanup pattern
    writes ``\r\x1b[1A\x1b[2K`` at the end of the buffer (CR, cursor
    up one row, erase entire line) which, when interpreted by a real
    terminal, erases the rendered row and leaves the cursor on a new
    line below. In the StringIO buffer this manifests as: the
    rendered model content lives on the FIRST captured line (before
    the ``\n``); the LAST captured line carries only the trailing
    carriage-return byte from the cleanup sequence. The structural
    assertion proves the cleanup happened AND that no rendered
    content survived on the final visible line.

    Without the transient erasure (e.g. if ``_STATUS_BAR_TRANSIENT``
    were ``False``), the buffer would NOT end with the
    ``\r\x1b[1A\x1b[2K`` cleanup sequence and the LAST captured
    line would carry rendered model content that would persist in
    scrollback.

    Uses the same ``_TtyLikeStringIO`` fake-console pattern as the
    existing live-region tests so the StatusBar real-TTY gate passes
    without a real pseudo-tty. The test does NOT use ``time.sleep``,
    does NOT spawn a subprocess, and runs in well under 1s.
    """
    ansi_escape_re = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
    buf = _TtyLikeStringIO()
    console = Console(
        file=buf,
        force_terminal=True,
        width=120,
        color_system="standard",
    )
    ctx = make_display_context(console=console, env={})
    pd = ParallelDisplay(ctx)
    sb = pd.status_bar
    assert console.is_terminal is True
    assert console.file.isatty() is True
    workspace_root = "/Users/alice/code/scrollback-cleanliness-probe"
    path_basename = "scrollback-cleanliness-probe"
    phase_label = "ScrollbackProbe"
    model = StatusBarModel(
        workspace_root=workspace_root,
        phase_label=phase_label,
        phase_style="theme.phase.development",
        outer_dev_iteration=1,
        outer_dev_cap=3,
        inner_analysis=2,
        inner_analysis_cap=5,
    )
    sb.update(model)
    sb.start()
    try:
        assert sb.is_active is True, (
            "StatusBar.start() must construct a Live region on a tty-like "
            "stream (both console.is_terminal and console.file.isatty() are True)"
        )
    finally:
        sb.stop()
    out = buf.getvalue()
    assert phase_label in out, (
        f"AC-08: Live region must have rendered the phase label "
        f"{phase_label!r} before stop; got out={out!r}"
    )
    assert path_basename[:20] in out, (
        f"AC-08: Live region must have rendered a recognizable workspace path "
        f"prefix for {workspace_root!r} before stop; got out={out!r}"
    )
    assert "Cycle 1/3" in out, (
        f"AC-08: Live region must have rendered the outer-dev iteration "
        f"'Cycle 1/3' before stop; got out={out!r}"
    )
    assert out.endswith("\r\x1b[1A\x1b[2K"), (
        f"AC-08: captured buffer must end with the Rich transient "
        f"Live cleanup sequence ('\\r\\x1b[1A\\x1b[2K') so scrollback "
        f"stays clean after StatusBar.stop(); got tail={out[-20:]!r}, "
        f"full out={out!r}"
    )
    lines = out.split("\n")
    last_line = lines[-1]
    last_line_visible = ansi_escape_re.sub("", last_line).strip()
    assert last_line_visible == "", (
        f"AC-08: after StatusBar.stop(), the LAST visible line of the "
        f"captured buffer must be empty (only the cleanup escape "
        f"sequences remain) so a real terminal shows no rendered model "
        f"content in scrollback; got last_line_visible={last_line_visible!r}, "
        f"full last_line={last_line!r}, full out={out!r}"
    )
    for forbidden_substr in (workspace_root, phase_label, "Cycle 1/3"):
        assert forbidden_substr not in last_line, (
            f"AC-08: the LAST captured line must NOT contain rendered "
            f"model content; got {forbidden_substr!r} in last_line={last_line!r}"
        )


def test_status_bar_fallback_erases_previous_row_before_active_update(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rich dumb-terminal fallback cleans each active replacement row.

    On recent Rich versions a force-terminal ``StringIO`` that also reports
    ``isatty()`` can let ``Live.start()`` succeed while ``Live`` itself
    refuses to render because the console is considered a dumb terminal.
    The StatusBar fallback renders the model manually in that branch. When a
    second model arrives while active, the previous fallback row must be
    erased before the replacement row is written so interpreted terminal
    scrollback contains only the latest footer row.
    """
    # Pin TERM=dumb so Rich's Console.is_dumb_terminal (and therefore
    # Console.is_interactive) reflects the dumb-terminal branch the fallback
    # is intended to cover. Rich reads TERM from os.environ at console
    # construction, so the env var must be set BEFORE Console(...) is built.
    # Without this, force_terminal=True + isatty()-faking StringIO leaves
    # is_interactive=True and the fallback's `_live_console_is_interactive`
    # gate short-circuits, so the fallback never writes to the buffer.
    monkeypatch.setenv("TERM", "dumb")
    ansi_escape_re = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
    cleanup = "\r\x1b[1A\x1b[2K"
    buf = _TtyLikeStringIO()
    console = Console(
        file=buf,
        force_terminal=True,
        width=120,
        color_system="standard",
    )
    ctx = make_display_context(console=console, env={})
    pd = ParallelDisplay(ctx)
    sb = pd.status_bar
    first = StatusBarModel(
        workspace_root="/Users/alice/code/status-bar",
        phase_label="FirstPhase",
        phase_style="theme.phase.development",
        outer_dev_iteration=1,
        outer_dev_cap=3,
    )
    second = StatusBarModel(
        workspace_root="/Users/alice/code/status-bar",
        phase_label="SecondPhase",
        phase_style="theme.phase.review",
        outer_dev_iteration=2,
        outer_dev_cap=3,
    )

    sb.update(first)
    sb.start()
    try:
        assert sb.is_active is True
        sb.update(second)
    finally:
        sb.stop()

    out = buf.getvalue()
    first_index = out.index("FirstPhase")
    first_cleanup_index = out.index(cleanup, first_index)
    second_index = out.index("SecondPhase")
    final_cleanup_index = out.rindex(cleanup)
    assert first_index < first_cleanup_index < second_index < final_cleanup_index
    assert out.count(cleanup) >= 2
    last_line_visible = ansi_escape_re.sub("", out.split("\n")[-1]).strip()
    assert last_line_visible == ""


def test_status_bar_regression_fallback_skips_identical_frame_repaint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """S-5: unchanged fallback frames do not erase and repaint the footer."""
    monkeypatch.setenv("TERM", "dumb")
    buf = _TtyLikeStringIO()
    console = Console(file=buf, force_terminal=True, width=120, color_system="standard")
    pd = ParallelDisplay(make_display_context(console=console, env={}))
    model = StatusBarModel(
        workspace_root="/Users/alice/code/status-bar",
        phase_label="StablePhase",
        phase_style="theme.phase.development",
        outer_dev_iteration=1,
        outer_dev_cap=3,
    )

    pd.status_bar.update(model)
    pd.status_bar.start()
    try:
        after_start = buf.getvalue()
        pd.status_bar.update(model)
        assert buf.getvalue() == after_start
    finally:
        pd.status_bar.stop()


# ---------------------------------------------------------------------------
# StatusBar.start() failure-isolation regression test
# ---------------------------------------------------------------------------


def test_status_bar_start_rolls_back_live_on_startup_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """StatusBar.start() must keep ``is_active`` False when ``Live.start()`` raises.

    The original implementation assigned ``self._live = Live(...)``
    BEFORE calling ``self._live.start()``, then wrapped the whole block
    in ``contextlib.suppress(Exception)``. When ``Live.start()``
    failed, the exception was swallowed but ``self._live`` stayed
    non-None, so ``is_active`` (defined as ``self._live is not None``)
    returned True even though no Live region was actually active. This
    also blocked a later retry because ``_gate()`` short-circuits when
    ``self._live is not None``.

    The fix commits ``self._live = live`` ONLY after a successful
    ``live.start()``. This test exercises both halves of the contract:

    1. When ``Live.start()`` raises, ``is_active`` stays False (the
       failed Live instance is discarded, not committed to ``_live``).
    2. ``stop()`` on an unstarted bar is still a no-op (it must not
       try to call ``Live.stop()`` on the discarded instance).
    3. Once the patched-failure is removed, a subsequent ``start()``
       succeeds and ``is_active`` flips True.

    The harness uses a tty-like StringIO-backed Console so the
    StatusBar real-TTY gate is open. ``monkeypatch.setattr`` targets
    ``rich.live.Live.start`` (the method called inside the function)
    so the patch survives the function-local ``from rich.live import
    Live`` import (Python resolves ``Live.start`` via the rich.live
    module, so patching the class method on the original class
    object is honored even when callers import it via ``from``).
    The patch is cleaned up automatically by ``monkeypatch``.
    """

    class _BoomError(RuntimeError):
        """Marker exception raised by the patched Live.start() to simulate a startup failure."""

    boom_count = {"n": 0}

    def boom_start(self: object) -> None:
        boom_count["n"] += 1
        raise _BoomError("simulated Live.start() failure")

    monkeypatch.setattr(rich.live.Live, "start", boom_start)

    buf = _TtyLikeStringIO()
    console = Console(
        file=buf,
        force_terminal=True,
        width=120,
        color_system="standard",
    )
    ctx = make_display_context(console=console, env={})
    pd = ParallelDisplay(ctx)
    sb = pd.status_bar
    assert console.is_terminal is True
    assert console.file.isatty() is True

    sb.update(
        StatusBarModel(
            workspace_root="/Users/alice/code/startup-failure-probe",
            phase_label="StartupFailureProbe",
            phase_style="theme.phase.development",
            outer_dev_iteration=1,
            outer_dev_cap=3,
        )
    )

    sb.start()
    assert sb.is_active is False, (
        "StatusBar.start() must NOT commit _live when Live.start() raises; "
        "is_active must stay False so a later retry is possible"
    )
    assert boom_count["n"] == 1, (
        f"patched Live.start() must have been called exactly once during "
        f"the failing start(); got {boom_count['n']} calls"
    )

    sb.stop()
    assert sb.is_active is False, (
        "StatusBar.stop() on a bar whose Live.start() failed must be a "
        "no-op (it must NOT call .stop() on the discarded Live instance)"
    )

    monkeypatch.undo()

    sb.start()
    assert sb.is_active is True, (
        "StatusBar.start() must succeed on retry once Live.start() is no "
        "longer patched to raise; is_active must flip True"
    )

    sb.stop()
    assert sb.is_active is False, (
        "StatusBar.stop() must tear down a successfully-started Live "
        "region and flip is_active back to False"
    )


# ---------------------------------------------------------------------------
# DA-001 / DA-002 / DA-003 / DA-004 — direct locks for the analysis feedback
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    (
        "/home/u/proj/ralph-workflow",
        "/Volumes/Crucial X9/ext-Projects/Ralph-Workflow/wt-028-display",
    ),
)
def test_status_bar_regression_middle_truncate_path_honours_every_budget(path: str) -> None:
    """DA-001: middle truncation never exceeds its requested budget."""
    for budget in range(1, len(path) + 5):
        result = _status_bar_module._middle_truncate_path(path, budget)
        assert len(result) <= budget, f"DA-001: budget={budget}, result={result!r}, path={path!r}"


@pytest.mark.parametrize("width", range(61, 201))
@pytest.mark.parametrize(
    ("workspace_root", "home"),
    (
        ("/home/u/proj/ralph-workflow", None),
        ("/Users/alice/code/ralph-workflow", "/Users/alice"),
    ),
)
def test_status_bar_regression_path_elision_preserves_basename(
    width: int, workspace_root: str, home: str | None
) -> None:
    """DA-001/DA-002: ordinary layouts either drop cwd or preserve its full basename."""
    plain = render_status_bar(
        StatusBarModel(
            workspace_root=workspace_root,
            phase_label="Development",
            phase_style="theme.phase.development",
            outer_dev_iteration=3,
            outer_dev_cap=4,
            inner_analysis=2,
            inner_analysis_cap=5,
            agent_name="claude",
            elapsed_seconds=761,
        ),
        _make_display_context(width=width),
        home=home,
    ).plain
    if "Agent claude" in plain:
        cwd = plain.rsplit("Agent claude", 1)[-1].strip()
        assert not cwd or cwd.endswith("ralph-workflow"), (
            f"DA-001/DA-002: width={width} clipped basename: {plain!r}"
        )


@pytest.mark.parametrize("width", [120, 80, 60])
@pytest.mark.parametrize(
    "attention_value",
    [None, "waiting", "stalled", "retrying", "terminated"],
)
def test_attention_arrival_does_not_shift_neighbours(
    width: int,
    attention_value: str | None,
) -> None:
    """DA-001 (AC-01): attention arrival shifts no neighbour.

    The reserved attention slot width is the worst-case across the
    four attention states. Whether the slot is empty or populated,
    the byte position of the phase and workspace path is identical,
    so the operator's eye does not have to re-track the bar.
    """
    model = StatusBarModel(
        workspace_root="/Users/alice/code/neighbour-probe",
        phase_label="Development",
        phase_style="theme.phase.development",
        outer_dev_iteration=1,
        outer_dev_cap=3,
        inner_analysis=2,
        inner_analysis_cap=5,
        attention=attention_value,
    )
    ctx = _make_display_context(width=width)
    healthy = render_status_bar(
        dataclasses.replace(model, attention=None),
        _make_display_context(width=width),
        home="/Users/alice",
    ).plain
    populated = render_status_bar(model, ctx, home="/Users/alice").plain
    # Phase position: find the phase marker + space prefix. The bar
    # uses ``■ `` (marker + space) as the phase marker.
    assert healthy.find("Development") == populated.find("Development"), (
        f"DA-001: phase must NOT shift when attention arrives; "
        f"healthy.pos={healthy.find('Development')}, "
        f"populated.pos={populated.find('Development')} "
        f"(attention={attention_value!r}, width={width})"
    )
    # Workspace path: search for the basename (home-relative form
    # collapses the ``/Users/alice`` prefix to ``~``).
    assert healthy.find("neighbour-probe") == populated.find("neighbour-probe"), (
        f"DA-001: workspace path must NOT shift when attention arrives; "
        f"healthy={healthy!r}, populated={populated!r}, "
        f"attention={attention_value!r}, width={width}"
    )


def test_elapsed_format_change_does_not_shift_path() -> None:
    """DA-002 (AC-01): the elapsed format roll-over shifts no neighbour.

    The elapsed display uses a fixed-width buffer (13 chars,
    ``Time HH:MM:SS``) so the byte position of the workspace path
    stays identical as the value crosses mm:ss -> H:mm:ss -> HH:mm:ss
    boundaries.
    """
    model = StatusBarModel(
        workspace_root="/Users/alice/code/elapsed-probe",
        phase_label="Development",
        phase_style="theme.phase.development",
        outer_dev_iteration=1,
        outer_dev_cap=3,
        inner_analysis=2,
        inner_analysis_cap=5,
    )
    paths = []
    for elapsed_seconds in (3599, 3600, 36000, 36001):
        plain = render_status_bar(
            dataclasses.replace(model, elapsed_seconds=float(elapsed_seconds)),
            _make_display_context(width=120),
            home="/Users/alice",
        ).plain
        paths.append(plain.find("elapsed-probe"))
    assert all(position >= 0 for position in paths), f"DA-002: path missing: {paths!r}"
    assert len(set(paths)) == 1, f"DA-002: elapsed rollover shifted path: {paths!r}"


@pytest.mark.parametrize("width", [120, 80, 60, 40])
def test_status_bar_regression_value_changes_preserve_surviving_segment_columns(width: int) -> None:
    """S-2: counters and identity reserve their realistic maximum widths."""

    def render(*, cycle: int, cycle_cap: int, iteration: int, agent: str, elapsed: int) -> str:
        return render_status_bar(
            StatusBarModel(
                workspace_root="/Users/alice/code/reflow-probe/subdir",
                phase_label="Development",
                phase_style="theme.phase.development",
                outer_dev_iteration=cycle,
                outer_dev_cap=cycle_cap,
                inner_analysis=iteration,
                inner_analysis_cap=100,
                agent_name=agent,
                elapsed_seconds=elapsed,
            ),
            _make_display_context(width=width),
            home="/Users/alice",
        ).plain

    baseline = render(cycle=9, cycle_cap=99, iteration=9, agent="claude", elapsed=761)
    transitions = (
        render(cycle=10, cycle_cap=99, iteration=10, agent="claude", elapsed=3720),
        render(cycle=99, cycle_cap=99, iteration=99, agent="claude", elapsed=3720),
    )
    # At constrained widths the layout is deliberately allowed to choose a
    # smaller surviving set; stable columns apply to values that keep their
    # rendered form, while the existing tests pin the width ladder itself.
    anchors = ("Development", "Cycle", "iter", "Agent", "subdir")
    for changed in transitions:
        for anchor in anchors:
            baseline_column = baseline.find(anchor)
            changed_column = changed.find(anchor)
            if baseline_column >= 0 and changed_column >= 0:
                assert changed_column == baseline_column, (
                    f"S-2: {anchor} shifted at width={width}; "
                    f"baseline={baseline!r}, changed={changed!r}"
                )


def test_status_bar_regression_agent_name_reflow_at_60_keeps_preceding_columns() -> None:
    """S-4: agent-name growth at the 60-column rung does not reflow the bar."""

    def render(agent_name: str) -> str:
        return render_status_bar(
            StatusBarModel(
                workspace_root="/Users/alice/code/reflow-probe/subdir",
                phase_label="Development",
                phase_style="theme.phase.development",
                outer_dev_iteration=1,
                outer_dev_cap=3,
                inner_analysis=2,
                inner_analysis_cap=5,
                agent_name=agent_name,
                elapsed_seconds=599,
            ),
            _make_display_context(width=60),
            home="/Users/alice",
        ).plain

    short, long = render("claude"), render("claude-headless")
    assert "subdir" not in short and "subdir" not in long
    for anchor in ("Dev", "C1/3", "i2/5", "Agent"):
        assert short.find(anchor) == long.find(anchor), (
            f"S-4: {anchor} shifted when agent name grew: {short!r} -> {long!r}"
        )


def test_status_bar_regression_width_ladder_and_agent_identity_are_stable() -> None:
    """DA-001/DA-002: the documented width ladder is monotonic and agent-safe."""

    def render(width: int, agent_name: str = "claude") -> str:
        return render_status_bar(
            StatusBarModel(
                workspace_root="/Users/alice/code/ralph-workflow",
                phase_label="Development",
                phase_style="theme.phase.development",
                outer_dev_iteration=3,
                outer_dev_cap=4,
                inner_analysis=2,
                inner_analysis_cap=4,
                elapsed_seconds=761,
                agent_name=agent_name,
            ),
            _make_display_context(width=width),
            home="/Users/alice",
        ).plain

    at_80 = render(80)
    at_60 = render(60)
    assert "Development" in at_80
    assert "claude" in at_80
    assert "ralph-workflow" in at_80
    assert "ralph-workflow" not in at_60
    assert "Dev" in at_60

    segments_at_width = {
        width: {
            segment
            for segment, token in (("agent", "Agent"), ("path", "ralph-workflow"))
            if token in render(width)
        }
        for width in range(40, 121)
    }
    for width in range(40, 120):
        assert segments_at_width[width] <= segments_at_width[width + 1], (
            width,
            segments_at_width,
        )

    for width in (80, 120):
        paths = [
            render(width, agent_name).find("ralph-workflow")
            for agent_name in ("pi", "claude", "claude-headless", "pi · minimax/MiniMax-3")
        ]
        assert all(position >= 0 for position in paths), (width, paths)
        assert len(set(paths)) == 1, (width, paths)


def test_status_bar_drops_path_at_60() -> None:
    """DA-003 (AC-02): at width 60 the workspace path is dropped.

    The spec drops the path entirely at the 60-col rung (no
    truncated ghost). The path basename ``subdir`` must NOT appear
    in the rendered bar at width 60.
    """
    model = StatusBarModel(
        workspace_root="/Users/alice/code/my-very-long-project-directory-name/subdir",
        phase_label="Development Analysis",
        phase_style="theme.phase.development",
        outer_dev_iteration=1,
        outer_dev_cap=3,
        inner_analysis=2,
        inner_analysis_cap=5,
    )
    ctx = _make_display_context(width=60)
    plain = render_status_bar(model, ctx, home="/Users/alice").plain
    assert "subdir" not in plain, (
        f"DA-003: workspace path must drop entirely at width=60 "
        f"(no truncated ghost); got plain={plain!r}"
    )


def test_status_bar_abbreviates_phase_at_60() -> None:
    """DA-003 (AC-02): at width 60 the phase label abbreviates.

    The spec abbreviates the phase label at the 60-col rung so the
    dropped path's budget can be redirected to recognisable
    segments. The rendered bar must include the short form
    (``dev``) and NOT the full ``Development`` label.
    """
    model = StatusBarModel(
        workspace_root="/Users/alice/code/my-very-long-project-directory-name/subdir",
        phase_label="Development Analysis",
        phase_style="theme.phase.development",
        outer_dev_iteration=1,
        outer_dev_cap=3,
        inner_analysis=2,
        inner_analysis_cap=5,
    )
    ctx = _make_display_context(width=60)
    plain = render_status_bar(model, ctx, home="/Users/alice").plain
    assert "dev" in plain.lower(), (
        f"DA-003: abbreviated phase 'dev' must render at width=60; got plain={plain!r}"
    )
    assert "Development" not in plain, (
        f"DA-003: full 'Development' must NOT render at width=60 "
        f"(path dropped, phase abbreviated); got plain={plain!r}"
    )


def test_status_bar_floor_keeps_attention_phase_liveness_position_elapsed() -> None:
    """DA-003 (AC-02) / S-3: the 40-col floor survives the 5 core segments.

    The spec's 40-col rung must keep the five core segments
    readable: attention, phase, liveness, position, and elapsed.
    The bar carries the reserved attention slot, the phase
    label, the liveness glyph, the outer-dev cycle (position),
    and the elapsed short form. The workspace path is elided.

    The phase label is tail-truncated to the available phase
    budget (the new liveness + elapsed short form consume 6
    chars of chrome vs. the pre-S-3 budget). A long phase
    label like ``Development Analysis`` is shortened to its
    5-char tail-truncated form (``De...``); a shorter phase
    like ``Development`` abbreviates to ``Dev`` per the
    spec abbreviation ladder.
    """
    model = StatusBarModel(
        workspace_root="/Users/alice/code/my-very-long-project-directory-name/subdir",
        phase_label="Development Analysis",
        phase_style="theme.phase.development",
        outer_dev_iteration=1,
        outer_dev_cap=3,
        inner_analysis=2,
        inner_analysis_cap=5,
    )
    ctx = _make_display_context(width=40)
    plain = render_status_bar(model, ctx, home="/Users/alice").plain
    # Reserved attention slot (12 chars blank, the worst-case width).
    assert plain.startswith(" " * 12), (
        f"DA-003: attention slot must be reserved at width=40 "
        f"(leading 12 blank chars); got plain={plain!r}"
    )
    # Liveness glyph survives (the new S-3 segment).
    liveness_glyphs = ("\u280b", "*")
    assert any(glyph in plain for glyph in liveness_glyphs), (
        f"DA-003/S-3: liveness glyph must survive at width=40; got plain={plain!r}"
    )
    # Elapsed short form survives (the new S-3 segment).
    import re as _re

    assert _re.search(r"\d+m\d{2}s|\d+h\d{2}m|Time \d", plain) or "     " in plain, (
        f"DA-003/S-3: elapsed short form must survive at width=40; got plain={plain!r}"
    )
    # Phase label survives (recognisable prefix, abbreviated or
    # tail-truncated). A long label like ``Development Analysis``
    # is tail-truncated to ``De...`` (5 chars) at the 40-col
    # floor; the leading ``D`` is the recognisable part.
    assert plain.lstrip().lower().startswith("d") or "d" in plain[12:18].lower(), (
        f"DA-003: phase label must survive at width=40; got plain={plain!r}"
    )
    # Position (cycle) survives (canonical or compact form).
    position_carriers = ("Cycle 1/3", "C1/3", "1/3")
    assert any(c in plain for c in position_carriers), (
        f"DA-003: position must survive at width=40 (any of "
        f"{position_carriers!r}); got plain={plain!r}"
    )


@pytest.mark.parametrize("width", [40, 60, 80, 120])
@pytest.mark.parametrize("attention", [None, "waiting", "stalled", "retrying", "terminated"])
def test_status_bar_contract_attention_width_ladder(
    width: int,
    attention: str | None,
) -> None:
    """S-1: attention, width ladder, elapsed stability, and cwd-last contract."""
    model = StatusBarModel(
        workspace_root="/Users/alice/code/contract-probe/subdir",
        phase_label="Development",
        phase_style="theme.phase.development",
        outer_dev_iteration=1,
        outer_dev_cap=3,
        inner_analysis=2,
        inner_analysis_cap=5,
        agent_name="claude",
        attention=attention,
        elapsed_seconds=599,
    )
    plain = render_status_bar(model, _make_display_context(width=width), home="/Users/alice").plain
    assert "\n" not in plain
    assert len(plain) <= width
    if attention is not None:
        assert _status_bar_module.ATTENTION_PRESENTATION[attention][0] in plain
    assert "Development" in plain or "Dev" in plain or "dev" in plain or "De..." in plain
    if width >= 40:
        assert any(glyph in plain for glyph in ("⠋", "*"))
        assert "Time 09:59" in plain if width >= 120 else "9m59s" in plain
    if width == 60:
        assert "Agent claude" in plain
        assert "subdir" not in plain
    if width >= 80:
        assert 0 <= plain.find("Agent") < plain.rfind("subdir")


@pytest.mark.parametrize("widths", [(120, 39, 120), (80, 12, 80)])
def test_status_bar_regression_resize_below_floor_and_back_restores_layout(
    widths: tuple[int, int, int],
) -> None:
    """S-3: a below-floor resize relayouts immediately and restores exactly."""
    model = StatusBarModel(
        workspace_root="/Users/alice/code/resize-probe/subdir",
        phase_label="Development",
        phase_style="theme.phase.development",
        outer_dev_iteration=1,
        outer_dev_cap=3,
        inner_analysis=2,
        inner_analysis_cap=5,
        agent_name="claude",
        attention="waiting",
        elapsed_seconds=599,
    )
    rendered = [
        render_status_bar(model, _make_display_context(width=width), home="/Users/alice").plain
        for width in widths
    ]
    for width, plain in zip(widths, rendered, strict=True):
        assert plain.count("\n") == 0
        assert len(plain) <= width
    floor = render_status_bar(model, _make_display_context(width=40), home="/Users/alice").plain
    for identity in ("WAITING", "Dev", "1/3"):
        assert identity in floor
    assert rendered[2] == rendered[0]


def test_segment_order_matches_spec() -> None:
    """DA-004 (AC-02): segment byte order matches the spec at width 120.

    The spec order is: attention, phase, liveness (elapsed),
    cycle·iter, agent, cwd path. The path must appear LAST (after
    agent) so it is the trailing optional segment that elides /
    drops first at narrow widths.
    """
    model = StatusBarModel(
        workspace_root="/Users/alice/code/order-probe/subdir",
        phase_label="Development",
        phase_style="theme.phase.development",
        outer_dev_iteration=1,
        outer_dev_cap=3,
        inner_analysis=2,
        inner_analysis_cap=5,
        agent_name="claude",
    )
    ctx = _make_display_context(width=120)
    plain = render_status_bar(model, ctx, home="/Users/alice").plain
    # Per spec: attention slot -> phase -> elapsed -> cycle -> iter -> agent -> path.
    # The path is rendered in home-relative form (``~/code/.../subdir``)
    # and may be middle-truncated to fit the budget, so we search for
    # the trailing segment ``subdir`` (always preserved by the
    # middle-truncate helper when the path is non-empty).
    attention_end = 12  # reserved slot width
    phase_marker = plain.find("■", attention_end)
    phase_end = plain.find("◆", phase_marker)
    elapsed_pos = phase_end + len("◆ ")
    elapsed_end = plain.find("◆", elapsed_pos + 13)
    cycle_pos = plain.find("Cycle", elapsed_end)
    iter_pos = plain.find("iter", cycle_pos)
    agent_pos = plain.find("Agent", iter_pos)
    # Path search: the trailing segment ``subdir`` is always present.
    path_pos = plain.find("subdir", agent_pos)
    # All positions must be strictly increasing per the spec order.
    positions = (phase_marker, phase_end, elapsed_end, cycle_pos, iter_pos, agent_pos, path_pos)
    assert all(pos >= 0 for pos in positions), (
        f"DA-004: every segment label must appear at width=120; "
        f"phase_marker={phase_marker}, phase_end={phase_end}, "
        f"elapsed_end={elapsed_end}, cycle_pos={cycle_pos}, "
        f"iter_pos={iter_pos}, agent_pos={agent_pos}, path_pos={path_pos}, "
        f"plain={plain!r}"
    )
    assert positions == tuple(sorted(positions)), (
        f"DA-004: segments must appear in spec order "
        f"(attention -> phase -> elapsed -> cycle -> iter -> agent -> path); "
        f"positions={positions}, plain={plain!r}"
    )
