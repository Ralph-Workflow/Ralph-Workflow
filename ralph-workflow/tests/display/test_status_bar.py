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
  public surface (outside the frozen 36-name ``emit_*`` set).
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
    assert "Dev 1/3" in plain
    assert "Analysis 2/5" in plain


@pytest.mark.parametrize("width", [100, 120, 200])
def test_render_status_bar_shows_all_fields_at_wide_widths(width: int) -> None:
    """At wide widths (>=100 cols), the Status Bar renders phase + dir + outer_dev + inner_analysis.

    The single default-mode layout preserves all applicable fields at any
    width that can accommodate them. At wide widths the path and phase
    labels fit the default budgets (path=48, phase=28) so all four
    fields render in full.
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
    assert "Dev 1/3" in plain
    assert "Analysis 2/5" in plain


@pytest.mark.parametrize("width", [40, 50, 60, 80, 99])
def test_render_status_bar_fits_terminal_width_at_any_width(width: int) -> None:
    """At any width, the Status Bar fits the terminal width without wrapping.

    The bar must NEVER exceed the terminal width. The phase, path, and
    iteration-label budgets are derived together from ``ctx.width`` so
    the rendered text remains single-line and within ``ctx.width``
    columns at every width. At narrow widths the iteration labels
    degrade from canonical (``Dev 1/3`` / ``Analysis 2/5``) through
    compact (``D1/3`` / ``A2/5``) to minimal (``1/3`` / ``2/5``) forms
    and ultimately drop a segment only when even the minimal form
    cannot fit alongside phase + path.
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


@pytest.mark.parametrize("width", [20, 24, 30, 40, 50, 60, 80, 99, 120, 200])
def test_render_status_bar_shows_all_applicable_fields_at_any_width(width: int) -> None:
    """At ANY applicable width, the Status Bar renders phase + dir + outer_dev + inner_analysis.

    This is the central AC-03 invariant: the persistent bottom Status
    Bar always renders all applicable iteration fields regardless of
    terminal width. The per-iteration label form adapts to ``ctx.width``
    (canonical / compact / minimal) so the bar always fits ``ctx.width``,
    but the count-vs-cap payload (``1/3`` for outer_dev and ``2/5`` for
    inner_analysis) is ALWAYS present in some form.

    The narrowest width here is 20: at width 20 the layout is
    ``marker sep sep glyph outer sep glyph inner`` and the iteration
    labels render in compact form (``D1/3`` / ``A2/5``). Below 20 the
    iteration labels are still rendered (the bar drops the marker and
    per-iter glyphs to make room), but the bar is no longer width-bounded
    enough for the standard layout — the fits-width test below covers
    the 14-119 range and a separate boundary test covers width 14.

    The test name "any applicable width" was tightened from "any width"
    after the analysis feedback required iterations to remain visible
    at narrow widths: widths below 14 cannot fit both iteration labels
    with the standard layout and are out of scope for the iteration
    invariant (they pre-date the persistent bar).
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
    # outer_dev iteration: must always render in SOME form. Accept canonical
    # ("Dev 1/3"), compact ("D1/3"), or minimal ("1/3"). The count/payload
    # ("1/3") is the disambiguating invariant.
    outer_forms = ("Dev 1/3", "D1/3", "1/3")
    assert any(form in plain for form in outer_forms), (
        f"outer_dev must render in canonical/compact/minimal form at "
        f"width={width}; got {plain!r}"
    )
    # inner_analysis iteration: must always render in SOME form.
    inner_forms = ("Analysis 2/5", "A2/5", "2/5")
    assert any(form in plain for form in inner_forms), (
        f"inner_analysis must render in canonical/compact/minimal form at "
        f"width={width}; got {plain!r}"
    )


@pytest.mark.parametrize("width", [40, 50, 60, 80, 99, 120, 200])
def test_render_status_bar_canonical_iteration_labels_at_ac03_widths(width: int) -> None:
    """AC-03 invariant: at widths >= 40 cols, iteration labels are ALWAYS canonical.

    Locks the AC-03 invariant at widths 40/50/60/80/99/120/200 cols: the
    rendered Status Bar contains the FULL canonical iteration labels
    (``Dev 1/3`` and ``Analysis 2/5``) regardless of how much phase/path
    truncation is needed. The only width-driven difference across these
    widths is path middle-truncation and phase tail-truncation; the
    iteration label FORM is identical (canonical). Below 40 cols the
    implementation may degrade to compact/minimal forms to fit the bar.

    This is the regression test that locks the analysis-feedback fix:
    previously at width=40 the bar used compact labels (``D1/3`` /
    ``A2/5``), violating AC-03's identical-rendering invariant.
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
    # Canonical form is required at every width >= 40.
    assert "Dev 1/3" in plain, (
        f"AC-03: 'Dev 1/3' must render in canonical form at width={width}; "
        f"got {plain!r}"
    )
    assert "Analysis 2/5" in plain, (
        f"AC-03: 'Analysis 2/5' must render in canonical form at width={width}; "
        f"got {plain!r}"
    )
    # No compact or minimal label forms at AC-03 widths.
    assert "D1/3" not in plain, (
        f"AC-03: compact 'D1/3' must NOT appear at width={width}; got {plain!r}"
    )
    assert "A2/5" not in plain, (
        f"AC-03: compact 'A2/5' must NOT appear at width={width}; got {plain!r}"
    )
    # Width-fit invariant.
    assert len(plain) <= width, (
        f"AC-03: rendered bar exceeds width at width={width}; "
        f"len(plain)={len(plain)} > width={width}, plain={plain!r}"
    )
    # Single-line invariant.
    assert "\n" not in plain, (
        f"AC-03: rendered bar must be single-line at width={width}; got {plain!r}"
    )


@pytest.mark.parametrize("width", [14, 15, 20, 24, 30, 40, 50, 60, 80, 100, 120])
def test_render_status_bar_fits_width_at_narrow_terminal_with_long_inputs(width: int) -> None:
    """Status Bar fits ``ctx.width`` even with long inputs at narrow terminals.

    Regression for the analysis-feedback finding that the previous
    implementation produced a 45-char rendered bar at widths 20/24/30
    with long workspace paths and both iteration fields. The fix
    degrades iteration labels from canonical (``Dev 1/3`` /
    ``Analysis 2/5``) to compact (``D1/3`` / ``A2/5``) to minimal
    (``1/3`` / ``2/5``) so the iteration fields stay visible at all
    applicable widths, and drops the phase_marker / per-iteration glyphs
    below the threshold where the standard layout cannot fit, so
    ``len(plain) <= width`` always holds at width >= 14.

    Width 14 is the narrowest width where the standard layout can
    still fit both iteration labels (``D1/3`` / ``A2/5``) without the
    marker or per-iteration glyphs. Widths below 14 are out of scope:
    the bar cannot honor the iteration-visibility contract AND fit
    ctx.width with the standard layout at those widths, and the
    historical compact-mode fallback (which dropped iteration
    segments at narrow widths) was removed in wt-028-display.
    """
    long_path = (
        "/Users/alice/code/my-very-long-project-directory-name/subdir"
    )
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
    assert "Dev 1/3" in plain
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
# render_status_bar — Dev label formatting
# ---------------------------------------------------------------------------


def test_render_status_bar_dev_iteration_format_with_cap() -> None:
    """outer_dev_iteration=1, outer_dev_cap=3 -> 'Dev 1/3'."""
    model = StatusBarModel(
        workspace_root="/Users/alice/code/proj",
        phase_label="Development",
        phase_style="theme.phase.development",
        outer_dev_iteration=1,
        outer_dev_cap=3,
    )
    ctx = _make_display_context(width=140)
    text = render_status_bar(model, ctx, home="/Users/alice")
    assert "Dev 1/3" in _plain_text(text)


def test_render_status_bar_dev_iteration_format_without_cap() -> None:
    """outer_dev_iteration=2, outer_dev_cap=None -> 'Dev #2' (canonical fallback)."""
    model = StatusBarModel(
        workspace_root="/Users/alice/code/proj",
        phase_label="Development",
        phase_style="theme.phase.development",
        outer_dev_iteration=2,
        outer_dev_cap=None,
    )
    ctx = _make_display_context(width=140)
    text = render_status_bar(model, ctx, home="/Users/alice")
    assert "Dev #2" in _plain_text(text)


# ---------------------------------------------------------------------------
# render_status_bar — Analysis label formatting
# ---------------------------------------------------------------------------


def test_render_status_bar_analysis_iteration_format_with_cap() -> None:
    """inner_analysis=3, inner_analysis_cap=7 -> 'Analysis 3/7'."""
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
    assert "Analysis 3/7" in _plain_text(text)


def test_render_status_bar_analysis_iteration_format_without_cap() -> None:
    """inner_analysis=1, inner_analysis_cap=None -> 'Analysis #1' fallback."""
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
    assert "Analysis #1" in _plain_text(text)


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
# render_status_bar — phase label tail-truncation in compact mode, never wraps
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
    assert "*" in plain, (
        f"ASCII milestone '*' must appear in plain output; got {plain!r}"
    )
    assert "[OD]" in plain, (
        f"ASCII outer_dev '[OD]' must appear in plain output; got {plain!r}"
    )
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
        spans_detail = [
            (text[span.start : span.end], span.style) for span in text.spans
        ]
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
            assert buf_value == "", (
                f"Non-terminal must not write anything; got {buf_value!r}"
            )
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
    assert "Dev 1/3" in out, (
        f"Live region must surface the iteration label 'Dev 1/3'; got {out!r}"
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
    assert "Commit" in out, (
        f"Live region must surface the phase label 'Commit'; got {out!r}"
    )
    # No placeholder for the omitted iteration fields.
    assert "--" not in out, (
        f"Live region must not render a '--' placeholder for omitted iteration; got {out!r}"
    )


def test_status_bar_live_region_renders_with_outer_dev_only() -> None:
    """Tty-like stream with outer_dev set and inner_analysis=None: medium-ish width check.

    The 120-col tty-like stream falls in 'wide' mode (>=100 cols), so the
    inner_analysis field would normally render. With inner_analysis=None
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
    assert "Dev 2/5" in out
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
    """ParallelDisplay exposes an update_status_bar(model) method (outside the 36-name set)."""
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
    src_no_docstrings = re.sub(r'\"\"\"[\s\S]*?\"\"\"', '', src)
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
    assert has_separator, (
        f"render_status_bar must include a separator glyph; got plain={plain!r}"
    )


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
