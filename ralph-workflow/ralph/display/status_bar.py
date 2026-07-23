"""Persistent Status Bar at the bottom of the interactive terminal display.

The Status Bar shows working directory, active phase, and any applicable
outer development iteration and inner analysis iteration during
interactive runs. It is the single owner of run-level layout, color,
spacing, truncation, and live-update behavior; the per-unit
``emit_status_line`` and the transient ``waiting_status_line`` are
orthogonal surfaces left intact for one-shot transcript lines.

After the wt-028-display consolidation, Ralph Workflow exposes exactly
ONE display mode (``default``). The persistent Status Bar always renders
all applicable fields at every terminal width where they fit:

- working directory (middle-truncated when long),
- active phase label (tail-truncated when long),
- outer development iteration (when non-``None`` AND ``ctx.width``
  can accommodate it),
- inner analysis iteration (when non-``None`` AND ``ctx.width``
  can accommodate it).

Width-driven degradation (in order) so ``len(text.plain) <= ctx.width``
holds at every width:

1. Path middle-truncation absorbs excess length on long paths.
2. Phase label tail-truncation absorbs excess length on long labels.
3. Iteration label form degrades canonical -> compact -> minimal.
4. Phase marker is dropped below the marker-fit threshold.
5. Per-iteration glyphs are dropped below the glyph-fit threshold.
6. Iteration segments drop one at a time (outer_dev first, then
   inner_analysis, then both) below the iteration-visibility
   threshold (``14 cols``). The bar always fits ``ctx.width`` even
   when iteration segments drop entirely \u2014 phase + path remain
   visible at every applicable width.

The bar is gated on a real-TTY check (``console.is_terminal AND
console.file.isatty()``) so it stays out of non-interactive runs
(redirects, pipes, CI logs, StringIO test consoles, and
force_terminal+StringIO consoles).

DI / purity invariants:

- ``render_status_bar`` is a pure function: no I/O, no env reads, no
  Console construction, no ``Path.home()`` calls (``home`` is a parameter
  so the function can be tested deterministically).
- ``status_bar.py`` does not construct a ``rich.Console`` and does not
  read ``os.environ`` / ``os.getenv``; the DI invariants test asserts this.
- The StatusBar lifecycle class lazily constructs a single
  ``rich.live.Live`` region only when the real-TTY gate passes; it
  never reads env at module import.

Cadence constants:

- ``_STATUS_BAR_REFRESH_PER_SECOND`` (default ``4.0``): refresh rate for
  the Live region. Pinned by
  ``test_status_bar_pins_steady_cadence_config``.
- ``_STATUS_BAR_TRANSIENT`` (default ``True``): frames are erased on
  stop, preserving clean scrollback, copy/paste, terminal search, and
  post-run log review.

Default rendering
-----------------

The single default layout renders (in order)::

    [phase_marker] {phase_label} [milestone] {workspace_root}
                              [milestone] {outer_dev} Dev N/cap
                              [milestone] {inner_analysis} Analysis N/cap

A field is omitted entirely (no ``--`` placeholder) when its iteration
field is ``None`` on the model. The phase marker glyph is omitted when
``ctx.glyphs_enabled`` is ``False`` so ASCII consoles render a clean
prefix.
"""

from __future__ import annotations

import contextlib
import os
import pathlib
import re
import threading
from dataclasses import dataclass
from typing import IO, TYPE_CHECKING, Protocol

from rich.text import Text

from ralph.display.phase_status import (
    format_analysis_cycle,
    format_analysis_cycle_compact,
    format_analysis_cycle_minimal,
    format_dev_cycle,
    format_dev_cycle_compact,
    format_dev_cycle_minimal,
)

if TYPE_CHECKING:
    from rich.live import Live as _Live

    from ralph.display.context import DisplayContext
    from ralph.display.parallel_display import ParallelDisplay


    class _StatusBarHost(Protocol):
        """Structural type for the ``display`` reference StatusBar composes against."""

        _ctx: DisplayContext
        _is_quiet: bool


_STATUS_BAR_REFRESH_PER_SECOND: float = 4.0
_STATUS_BAR_TRANSIENT: bool = True


DEFAULT_PATH_BUDGET: int = 48
DEFAULT_PHASE_LABEL_BUDGET: int = 28
_HOME_PREFIX: str = "~"
_ELLIPSIS: str = "..."
_ELLIPSIS_LEN: int = len(_ELLIPSIS)
_MIN_BUDGET: int = _ELLIPSIS_LEN + 1

# Status Bar hostile-input scrubber. The persistent live footer is
# single-line by contract (see ``render_status_bar``'s docstring) so
# anything that would split it across lines or inject terminal control
# sequences has to be neutralized before the label is appended to the
# rendered Text. The strips preserve the label's visual meaning as
# much as possible:
#   * CRLF / LF / CR collapse to a single ASCII space so a stray
#     newline can never wrap the bar into the working area.
#   * ASCII DEL and the C0 control block (including ``\t``) are
#     replaced with a single ASCII space so embedded NULs, BELs, and
#     tabs cannot poison the live region. ``\t`` is included here
#     because the bar width budget accounts column count via ``len()``
#     while a terminal expands ``\t`` to the next tab stop (typically
#     8 columns), which would otherwise blow up alignment / truncation
#     for any tab-containing path or phase label.
#   * CSI / SGR escape sequences (``ESC[...m`` and friends) are
#     stripped so a hostile path cannot inject color or cursor moves
#     into the bar.
_SAFE_LINE_NEWLINE_RE: re.Pattern[str] = re.compile(r"[\r\n]+")
_SAFE_LINE_CONTROL_RE: re.Pattern[str] = re.compile(r"[\x00-\x09\x0b-\x1f\x7f]")
_SAFE_LINE_ESCAPE_RE: re.Pattern[str] = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")


def _safe_single_line(text: str) -> str:
    """Return ``text`` reduced to one safe visual line.

    Neutralises three hostile-input classes that the live Status Bar
    cannot tolerate (see :data:`_SAFE_LINE_NEWLINE_RE`,
    :data:`_SAFE_LINE_CONTROL_RE`, :data:`_SAFE_LINE_ESCAPE_RE`).
    Collapses line breaks AND tab characters AND other C0 control
    bytes to a single ASCII space (preserving readability), and drops
    CSI escape sequences entirely. The tab-to-space normalization is
    required because the bar's width budget accounts column count via
    ``len()`` (terminal-cell-aware measurement is intentionally
    out-of-scope here) while a terminal expands ``\t`` to the next
    tab stop; without this normalization a single tab in a path or
    phase label would silently inflate the rendered width and break
    the ``len(text.plain) <= ctx.width`` invariant the Live region
    is sized against. Leading / trailing whitespace is trimmed so a
    path that is otherwise non-empty cannot render as an invisible
    bar segment.
    """
    if not text:
        return ""
    cleaned = _SAFE_LINE_ESCAPE_RE.sub("", text)
    cleaned = _SAFE_LINE_CONTROL_RE.sub(" ", cleaned)
    cleaned = _SAFE_LINE_NEWLINE_RE.sub(" ", cleaned)
    return cleaned.strip()

# Canonical label widths (full form: ``Dev 1/3`` / ``Analysis 2/5``).
# These reflect the WORST-CASE actual label length with multi-digit
# caps (e.g. ``Dev 99/999`` is 10 chars; ``Analysis 99/999`` is 14
# chars). The budget allocator reserves exactly these widths, so the
# canonical form fits even at the narrowest AC-03 width (40 cols)
# where the label MUST render (only path/phase truncation adapts to
# width — the AC-03 invariant).
_OUTER_DEV_LABEL_MAX_CHARS: int = 10
_INNER_ANALYSIS_LABEL_MAX_CHARS: int = 14
# Compact label widths (D1/3 / A2/5).
_OUTER_DEV_LABEL_COMPACT_MAX_CHARS: int = 4
_INNER_ANALYSIS_LABEL_COMPACT_MAX_CHARS: int = 4
# Minimal label widths (1/3 / 2/5; no prefix).
_OUTER_DEV_LABEL_MINIMAL_MAX_CHARS: int = 4
_INNER_ANALYSIS_LABEL_MINIMAL_MAX_CHARS: int = 4
# Threshold at and above which the canonical (full) label form is
# always honored regardless of how much phase/path truncation is
# needed. Below this threshold the implementation may degrade to
# compact/minimal forms when canonical labels cannot fit alongside
# phase + path at the terminal width.
_CANONICAL_FIT_THRESHOLD: int = 40

# Minimum readable budget for the workspace path and the phase label.
# The single default-mode Status Bar ALWAYS reserves at least this much
# space for the workspace path and the phase label so the operator can
# identify the active working directory and phase at every applicable
# width (the AC-07 narrow-terminal invariant). Below this combined
# minimum the iteration segments are dropped so the bar degrades cleanly
# to ``workspace + phase`` (or, at very narrow widths, to an empty bar).
# These minima align with the budgets the existing tail-truncate /
# middle-truncate helpers honour: ``_tail_truncate`` returns at least
# the first ``budget`` characters when ``budget <= _ELLIPSIS_LEN``,
# and ``_middle_truncate_path`` returns the trailing path segment
# tail-truncated to ``budget`` characters when ``budget <= _MIN_BUDGET``.
_MIN_PHASE_BUDGET: int = 5
_MIN_PATH_BUDGET: int = 4
_MIN_PHASE_PLUS_PATH: int = _MIN_PHASE_BUDGET + _MIN_PATH_BUDGET


@dataclass(frozen=True)
class StatusBarModel:
    """Immutable view-model for the persistent Status Bar footer.

    Attributes:
        workspace_root: Working-directory path to display.
        phase_label: Human-readable phase label (e.g. ``Development``).
        phase_style: Rich style string applied to the phase label
            (e.g. ``theme.phase.development``); also carries textual
            meaning so the bar is readable when color is disabled.
        outer_dev_iteration: Current outer cycle (1-indexed), or ``None``
            when the active phase does not track outer progress.
        outer_dev_cap: Outer cycle cap, or ``None`` when unknown.
        inner_analysis: Current inner analysis iteration (1-indexed),
            or ``None`` when the active phase does not track analysis cycles.
        inner_analysis_cap: Inner analysis iteration cap, or ``None`` when
            unknown.
        integration_alert: Operator-facing alert rendered as a leading
            bar segment while an auto-integrate conflict is unresolved
            (``None`` otherwise). Present so a run that needs conflict
            resolution can never scroll its warning out of sight.
        outer_label: Optional phase-appropriate label for the outer cycle
            (``None`` -> the neutral ``Cycle`` label via
            :func:`ralph.display.phase_status.format_dev_cycle`).
            Set to ``Remediation`` / ``Round`` etc. by callers whose
            phase-level semantics want a different noun than the default
            neutral label, so the bar never claims a phase is something
            it isn't (AC-02). When ``None`` the bar renders ``Cycle N/cap``;
            when set, it renders ``<outer_label> N/cap``.
    """

    workspace_root: str
    phase_label: str
    phase_style: str
    outer_dev_iteration: int | None = None
    outer_dev_cap: int | None = None
    inner_analysis: int | None = None
    inner_analysis_cap: int | None = None
    integration_alert: str | None = None
    outer_label: str | None = None


def _home_relative(path: str, home: str | None) -> str:
    """Return ``path`` with the ``home`` prefix replaced by ``~`` when applicable.

    When ``home`` is ``None``, the path passes through unchanged.
    """
    if home is None or not home:
        return path
    home_str = str(home)
    if path == home_str:
        return _HOME_PREFIX
    if path.startswith(home_str + os.sep):
        return _HOME_PREFIX + path[len(home_str):]
    return path


def _middle_truncate_path(path: str, budget: int) -> str:
    """Return ``path`` truncated to at most ``budget`` characters via middle-ellipsis.

    The first N characters and the final path segment are preserved when the
    path is too long. If the path fits in ``budget`` it is returned unchanged.
    If the budget cannot accommodate both a prefix and the last segment
    plus an ellipsis separator, the last segment is preferred (returned
    tail-truncated if necessary) so the user can still identify the
    project from the trailing path component.

    Invariant: ``len(returned) <= budget`` always holds.
    """
    if len(path) <= budget:
        return path
    last_sep = path.rfind(os.sep)
    last_segment = path[last_sep + 1:] if last_sep >= 0 else path
    last_segment_len = len(last_segment)
    separator_budget = _ELLIPSIS_LEN + 1  # ``.../``

    def _tail_truncated_segment() -> str:
        tail_avail = max(0, budget - _ELLIPSIS_LEN)
        if tail_avail == 0 or budget >= last_segment_len:
            return last_segment[:budget]
        return last_segment[:tail_avail].rstrip() + "..."

    if budget <= _MIN_BUDGET:
        return _tail_truncated_segment()
    if budget - separator_budget < last_segment_len:
        return _tail_truncated_segment()
    prefix_budget = budget - last_segment_len - separator_budget
    if prefix_budget <= 0:
        return last_segment[:budget]
    if prefix_budget >= len(path) - last_segment_len - 1:
        return path
    return f"{path[:prefix_budget]}.../{last_segment}"


def _tail_truncate(text: str, budget: int) -> str:
    """Return ``text`` tail-truncated to ``budget`` chars ending with ``...``.

    Trailing whitespace is stripped to keep the ellipsis visually clean.
    If ``text`` fits in ``budget`` it is returned unchanged.
    """
    if len(text) <= budget:
        return text
    if budget <= _ELLIPSIS_LEN:
        return text[:budget]
    return text[: budget - _ELLIPSIS_LEN].rstrip() + "..."


def _field_separator(ctx: DisplayContext) -> str:
    """Return the field-to-field separator for the Status Bar.

    The separator is always ``ctx.glyph_for('milestone')`` plus a trailing
    space, so the same logical glyph drives the visual rhythm on both
    Unicode (``◆ ``) and ASCII (``* ``) consoles. The ASCII fallback
    preserves scan-friendly consistency with the existing milestone
    glyphs used elsewhere in the display surface.
    """
    return f"{ctx.glyph_for('milestone')} "


def _iteration_segment_width(
    ctx: DisplayContext,
    *,
    glyph_key: str,
    label_max_chars: int,
) -> int:
    """Return the on-screen width of a single iteration segment.

    Each segment is rendered as ``separator + glyph + ' ' + label``.
    ``label_max_chars`` is the per-segment label budget chosen by the
    caller (canonical / compact / minimal form).
    """
    separator = _field_separator(ctx)
    glyph = ctx.glyph_for(glyph_key)
    return len(separator) + len(glyph) + 1 + label_max_chars


@dataclass(frozen=True)
class _FieldBudgets:
    """Width-aware rendering budgets derived from ``ctx.width``.

    The single default-mode Status Bar renders phase + dir + (any
    applicable outer_dev) + (any applicable inner_analysis) at every
    width where the iteration segments fit.

    AC-03 invariant: at widths >= ``_CANONICAL_FIT_THRESHOLD`` (40
    cols) the iteration label form is ALWAYS the canonical
    (``Dev 1/3`` / ``Analysis 2/5``) form regardless of how much
    phase/path truncation is needed. Only path middle-truncation and
    phase tail-truncation budgets adapt to width at those widths.

    Below ``_CANONICAL_FIT_THRESHOLD`` the implementation may degrade
    to compact (``D1/3`` / ``A2/5``) or minimal (``1/3`` / ``2/5``)
    forms when canonical labels cannot fit alongside phase + path at
    the terminal width.

    Below the iteration-visibility threshold (``14 cols``) the
    implementation drops iteration segments (outer_dev first, then
    inner_analysis, then both) one at a time so the bar degrades
    cleanly to phase + path. The phase and path budgets adapt to
    whatever space remains after the iteration segments are sized,
    so the rendered text always fits ``ctx.width`` (no wrap, no
    overflow). At very narrow widths the bar drops the phase_marker
    and the per-iteration glyphs (``render_marker=False``,
    ``render_iter_glyph=False``) to keep both iteration labels
    visible: a 14-col bar may render as ``1/3 2/5`` instead of the
    canonical ``■ Dev 1/3 ◆ ◎ Analysis 2/5`` at 100+ cols.
    """

    phase_budget: int
    path_budget: int
    outer_dev_label_max_chars: int
    inner_analysis_label_max_chars: int
    render_marker: bool
    render_iter_glyph: bool


def _field_overhead_and_label_budgets(
    ctx: DisplayContext,
    *,
    has_outer_dev: bool,
    has_inner_analysis: bool,
) -> _FieldBudgets:
    """Derive width-aware budgets that always fit ``ctx.width``.

    AC-03 invariant: at widths >= ``_CANONICAL_FIT_THRESHOLD`` (40 cols)
    the iteration label form is ALWAYS canonical (``Dev N/cap`` /
    ``Analysis N/cap``); only path middle-truncation and phase
    tail-truncation budgets adapt to width. Below the threshold the
    implementation may degrade to compact (``D1/3`` / ``A2/5``) or
    minimal (``1/3`` / ``2/5``) forms to fit the bar at very narrow
    widths.

    Iteration segments are present (in canonical / compact / minimal
    form) when the model fields are non-``None`` and ``ctx.width``
    can accommodate them. The function picks the most descriptive
    layout that fits ``ctx.width``:

    1. At widths ``>= _CANONICAL_FIT_THRESHOLD`` the canonical
       ``Dev N/cap`` / ``Analysis N/cap`` labels ALWAYS render in full
       with the marker and per-iteration glyphs (phase/path truncate
       to absorb any remaining width pressure).
    2. Below the canonical-fit threshold, the compact form
       (``D1/3`` / ``A2/5``) is used when it fits.
    3. Below the compact threshold, the minimal form (``1/3`` / ``2/5``)
       is used when it fits.
    4. Below the minimal-with-marker threshold, the phase_marker is
       dropped (``render_marker=False``) to recover two characters.
    5. Below the no-marker threshold, the per-iteration glyphs are
       dropped (``render_iter_glyph=False``) so the labels still fit
       alongside phase + path at very narrow widths.
    6. Below the iteration-visibility threshold (``<14`` cols), the
       iteration segments drop one at a time (outer_dev first, then
       inner_analysis, then both) so the bar degrades cleanly to
       whatever subset of phase + path can fit. The
       ``len(text.plain) <= ctx.width`` invariant holds at every
       width \u2014 the bar may drop iteration segments entirely below
       14 cols, but it never overflows.

    AC-07 invariant: at every applicable width the workspace path and
    phase label remain readable — the budget allocator reserves at
    least ``_MIN_PHASE_BUDGET`` chars for phase and ``_MIN_PATH_BUDGET``
    chars for path before iteration labels are sized, so the bar never
    collapses phase + path to zero (the AC-07 narrow-terminal
    contract).

    The phase and path budgets adapt to whatever space remains after
    the iteration segments are sized; they are AT LEAST
    ``_MIN_PHASE_BUDGET`` and ``_MIN_PATH_BUDGET`` respectively. The
    rendered text always fits ``ctx.width`` (no wrap, no overflow),
    and the iteration labels are present when the model fields are
    non-``None`` AND ``ctx.width`` can accommodate them alongside the
    protected phase + path budgets.

    Args:
        ctx: Display context providing glyphs and width.
        has_outer_dev: True when the model has an outer_dev field.
        has_inner_analysis: True when the model has an inner_analysis field.

    Returns:
        _FieldBudgets with phase_budget, path_budget, label budgets,
        and the render_marker / render_iter_glyph degradation flags.
    """
    separator_len = len(_field_separator(ctx))
    marker_len = len(ctx.glyph_for("phase_marker") + " ") if ctx.glyphs_enabled else 0
    outer_dev_glyph_len = len(ctx.glyph_for("outer_dev"))
    inner_analysis_glyph_len = len(ctx.glyph_for("inner_analysis"))

    def _iter_width(
        outer_label: int,
        inner_label: int,
        with_glyph: bool,
        *,
        include_outer: bool = True,
        include_inner: bool = True,
    ) -> int:
        """Per-iteration overhead (leading separator + glyph + space + label).

        Each iteration segment renders as ``separator + [glyph + " "] + label``.
        The leading separator is included here so the base chrome (marker +
        phase|path separator) does not double-count the trailing separator.

        ``include_outer`` / ``include_inner`` let the caller drop a segment
        entirely (no separator, no glyph, no label) at very narrow widths
        so the bar degrades cleanly without overflowing.
        """
        total = 0
        if has_outer_dev and include_outer:
            total += separator_len + outer_label
            if with_glyph:
                total += outer_dev_glyph_len + 1
        if has_inner_analysis and include_inner:
            total += separator_len + inner_label
            if with_glyph:
                total += inner_analysis_glyph_len + 1
        return total

    def _chrome(
        outer_label: int,
        inner_label: int,
        with_marker: bool,
        with_glyph: bool,
        *,
        include_outer: bool = True,
        include_inner: bool = True,
    ) -> int:
        """Total chrome excluding phase + path: marker + sep + iter segments."""
        ml = marker_len if with_marker else 0
        return ml + separator_len + _iter_width(
            outer_label,
            inner_label,
            with_glyph,
            include_outer=include_outer,
            include_inner=include_inner,
        )

    def _allocate(
        outer_label: int,
        inner_label: int,
        with_marker: bool,
        with_glyph: bool,
        *,
        include_outer: bool = True,
        include_inner: bool = True,
    ) -> _FieldBudgets | None:
        """Allocate a budget that fits ``ctx.width`` with at least the phase+path minima.

        Returns ``None`` when the requested iter configuration cannot
        fit alongside the protected phase + path minima at
        ``ctx.width`` (the caller tries the next iter configuration
        in priority order).
        """
        available = ctx.width - _chrome(
            outer_label,
            inner_label,
            with_marker,
            with_glyph,
            include_outer=include_outer,
            include_inner=include_inner,
        )
        if available < _MIN_PHASE_PLUS_PATH:
            return None
        # Allocate remaining space to phase + path. Phase gets up to
        # DEFAULT_PHASE_LABEL_BUDGET chars (tail-truncated by the
        # caller); anything beyond that goes to path. When the bar
        # cannot afford the default phase cap, phase gets whatever
        # remains after reserving the path minimum so the workspace
        # path stays readable per AC-07.
        if available - _MIN_PATH_BUDGET >= DEFAULT_PHASE_LABEL_BUDGET:
            phase_budget = DEFAULT_PHASE_LABEL_BUDGET
            path_budget = available - phase_budget
        else:
            phase_budget = available - _MIN_PATH_BUDGET
            path_budget = _MIN_PATH_BUDGET
        # Clamp: both phase and path must meet the AC-07 minimum. If
        # available is exactly the minimum (so phase + path each get
        # their minimum), the allocation above honours it; if not,
        # the safety clamp below catches the corner case where
        # DEFAULT_PHASE_LABEL_BUDGET < _MIN_PHASE_BUDGET.
        if phase_budget < _MIN_PHASE_BUDGET:
            phase_budget = _MIN_PHASE_BUDGET
            path_budget = available - phase_budget
        if path_budget < _MIN_PATH_BUDGET:
            path_budget = _MIN_PATH_BUDGET
            phase_budget = available - path_budget
        if phase_budget < _MIN_PHASE_BUDGET or path_budget < _MIN_PATH_BUDGET:
            return None
        return _FieldBudgets(
            phase_budget,
            path_budget,
            outer_label if include_outer else 0,
            inner_label if include_inner else 0,
            with_marker,
            with_glyph,
        )

    label_forms: tuple[tuple[int, int], ...] = (
        (_OUTER_DEV_LABEL_MAX_CHARS, _INNER_ANALYSIS_LABEL_MAX_CHARS),
        (_OUTER_DEV_LABEL_COMPACT_MAX_CHARS, _INNER_ANALYSIS_LABEL_COMPACT_MAX_CHARS),
        (_OUTER_DEV_LABEL_MINIMAL_MAX_CHARS, _INNER_ANALYSIS_LABEL_MINIMAL_MAX_CHARS),
    )

    # Iter-bearing layouts (both segments preferred; degrade to a
    # single segment when both cannot fit alongside phase + path).
    iter_bearing_configs: tuple[tuple[bool, bool], ...] = (
        (True, True),
        (True, False),
        (False, True),
    )
    for include_outer, include_inner in iter_bearing_configs:
        for outer_label, inner_label in label_forms:
            for with_marker in (True, False):
                for with_glyph in (True, False):
                    budget = _allocate(
                        outer_label,
                        inner_label,
                        with_marker,
                        with_glyph,
                        include_outer=include_outer,
                        include_inner=include_inner,
                    )
                    if budget is not None:
                        return budget

    # Workspace + phase only (AC-07 fallback): drop both iter segments
    # when they cannot fit alongside the protected phase + path
    # budgets. The marker may still render if it fits alongside the
    # minimum phase + path budgets; otherwise the marker is dropped.
    for with_marker in (True, False):
        budget = _allocate(
            _OUTER_DEV_LABEL_MINIMAL_MAX_CHARS,
            _INNER_ANALYSIS_LABEL_MINIMAL_MAX_CHARS,
            with_marker,
            with_glyph=False,
            include_outer=False,
            include_inner=False,
        )
        if budget is not None:
            return budget

    # Final fallback: width is so narrow that the phase + path
    # minimum cannot be honoured. Render an empty bar (the
    # ``render_status_bar`` caller clamps the rendered text to
    # ``ctx.width`` so the no-overflow invariant still holds).
    return _FieldBudgets(
        0,
        0,
        0,
        0,
        False,
        False,
    )


def _format_dev_label(
    n: int,
    cap: int | None,
    max_chars: int,
    *,
    outer_label: str | None = None,
) -> str:
    """Format the outer-cycle label using the form that fits ``max_chars``.

    When ``outer_label`` is provided (e.g. ``Remediation`` for policy
    remediation, ``Round`` for conflict resolution), the canonical and
    compact forms substitute the supplied noun for the neutral
    ``Cycle`` / ``C`` prefix; the minimal form has no prefix to swap
    and is returned as-is. This keeps the per-iteration redundancy the
    status bar already provides (a glyph + an ASCII label) while letting
    callers choose a phase-appropriate noun.
    """
    if max_chars <= 0:
        return ""
    if max_chars >= _OUTER_DEV_LABEL_MAX_CHARS:
        label = format_dev_cycle(n, cap)
        if outer_label:
            # replace the default 'Cycle' / 'N' label with the
            # caller's noun. Safe because format_dev_cycle produces
            # ``Cycle N/cap`` / ``Cycle #N`` -- both start with 'Cycle '.
            return f"{outer_label} {label.split(' ', 1)[1]}"
        return label
    if max_chars >= _OUTER_DEV_LABEL_COMPACT_MAX_CHARS:
        label = format_dev_cycle_compact(n, cap)
        if outer_label:
            # Compact form is 'C{n}/{cap}' / 'C#{n}' -- swap the 'C'
            # prefix for the first char of the supplied noun when it is
            # exactly one character long; otherwise fall back to the
            # neutral compact label (avoid truncation hazards at this
            # tight 4-char budget).
            initial = outer_label[:1]
            return f"{initial}{label[1:]}"
        return label
    return format_dev_cycle_minimal(n, cap)


def _format_analysis_label(n: int, cap: int | None, max_chars: int) -> str:
    """Format the inner_analysis label using the form that fits ``max_chars``."""
    if max_chars <= 0:
        return ""
    if max_chars >= _INNER_ANALYSIS_LABEL_MAX_CHARS:
        return format_analysis_cycle(n, cap)
    if max_chars >= _INNER_ANALYSIS_LABEL_COMPACT_MAX_CHARS:
        return format_analysis_cycle_compact(n, cap)
    return format_analysis_cycle_minimal(n, cap)


def render_status_bar(
    model: StatusBarModel,
    ctx: DisplayContext,
    *,
    home: str | None = None,
) -> Text:
    """Render the single-line Status Bar footer for the given model.

    This function is PURE: no I/O, no env reads, no Console construction,
    no ``Path.home()`` calls. ``home`` is a parameter so callers can
    supply the resolved home directory once (the ``StatusBar`` lifecycle
    resolves it at construction; tests pass an explicit value).

    The single default-mode layout renders phase + dir + (any applicable
    outer_dev) + (any applicable inner_analysis) at every width where
    the iteration segments fit. When ``ctx.width`` is too narrow to fit
    the canonical forms (``Dev 1/3`` / ``Analysis 2/5``) the labels
    degrade through compact (``D1/3`` / ``A2/5``) and minimal
    (``1/3`` / ``2/5``) forms, the phase marker and per-iteration
    glyphs are dropped at the marker-fit / glyph-fit thresholds, and
    finally the iteration segments drop one at a time at very narrow
    widths (below ``14 cols``) so the bar still fits ``ctx.width``.

    The phase and path labels are tail/middle truncated to fit the
    remaining budget. ``len(text.plain) <= ctx.width`` always holds
    (a final ``Text.truncate`` clamp covers the 1-2 col edge case
    where the phase|path separator alone exceeds the budget), and the
    rendered text never contains a newline.

    Args:
        model: Immutable view-model describing the bar contents.
        ctx: Display context providing mode, glyphs, and theme-aware style.
        home: Optional home directory; when supplied and
            ``model.workspace_root`` starts with it, the rendered path is
            home-relative.

    Returns:
        A single-line ``rich.text.Text`` carrying the bar contents. The
        rendered text never contains ``\\n`` so the bar cannot wrap into
        the working area, and ``len(text.plain) <= ctx.width`` so the
        bar fits any terminal width (including widths below 14 cols
        where iteration segments drop entirely to honor the
        ``len(text.plain) <= ctx.width`` invariant).
    """
    separator = _field_separator(ctx)
    # Neutralise hostile input in the user-facing labels BEFORE any
    # truncation or budget allocation. ``_safe_single_line`` strips
    # CR / LF, C0 control bytes, and CSI / SGR escape sequences from
    # the strings so a stray newline in ``phase_label`` or an
    # ``ESC[31m`` in ``workspace_root`` cannot split the bar into the
    # working area or inject terminal control codes into the live
    # region. The rendered text is therefore single-line by
    # construction (the function-level ``text.truncate`` clamp is a
    # width safety net, not a newline safety net).
    path_display = _safe_single_line(_home_relative(model.workspace_root, home))
    phase_display = _safe_single_line(model.phase_label)

    has_outer_dev = model.outer_dev_iteration is not None
    has_inner_analysis = model.inner_analysis is not None
    budgets = _field_overhead_and_label_budgets(
        ctx,
        has_outer_dev=has_outer_dev,
        has_inner_analysis=has_inner_analysis,
    )

    path_display = _middle_truncate_path(path_display, budgets.path_budget)
    phase_display = _tail_truncate(phase_display, budgets.phase_budget)
    render_outer_dev = has_outer_dev and budgets.outer_dev_label_max_chars > 0
    render_inner_analysis = has_inner_analysis and budgets.inner_analysis_label_max_chars > 0
    text = Text()
    if model.integration_alert:
        # The alert LEADS the bar so an unresolved integration conflict
        # is visible at every width; the final width clamp below still
        # bounds the rendered line. Sanitized like every other segment.
        alert_display = _safe_single_line(model.integration_alert)
        text.append(
            ctx.glyph_for("warning") + " " + alert_display,
            style="theme.status.error",
        )
        text.append(separator, style="theme.status.path_marker")
    if ctx.glyphs_enabled and budgets.render_marker:
        marker = ctx.glyph_for("phase_marker")
        text.append(marker + " ", style="theme.status.bar_marker")
    text.append(phase_display, style=model.phase_style)
    text.append(separator, style="theme.status.path_marker")
    text.append(path_display, style="theme.status.path")
    if render_outer_dev:
        text.append(separator, style="theme.status.path_marker")
        if budgets.render_iter_glyph:
            text.append(ctx.glyph_for("outer_dev") + " ", style="theme.outer_dev")
        text.append(
            _format_dev_label(
                model.outer_dev_iteration or 0,
                model.outer_dev_cap,
                budgets.outer_dev_label_max_chars,
                outer_label=model.outer_label,
            )
        )
    if render_inner_analysis:
        text.append(separator, style="theme.status.path_marker")
        if budgets.render_iter_glyph:
            text.append(
                ctx.glyph_for("inner_analysis") + " ", style="theme.inner_analysis"
            )
        text.append(
            _format_analysis_label(
                model.inner_analysis or 0,
                model.inner_analysis_cap,
                budgets.inner_analysis_label_max_chars,
            )
        )
    # Final width clamp: at extremely narrow widths (1-2 cols) the
    # phase|path separator alone exceeds the budget so the rendered text
    # cannot fit. Truncate the rendered text to ``ctx.width`` so the
    # ``len(text.plain) <= ctx.width`` invariant holds at every width,
    # including widths below the iteration-visibility threshold (14 cols).
    if ctx.width < 1:
        return Text(" ")
    if len(text.plain) > ctx.width:
        text.truncate(ctx.width)
    return text


class StatusBar:
    """Lifecycle owner for the persistent bottom Status Bar.

    The StatusBar is composed by :class:`ralph.display.parallel_display.ParallelDisplay`
    and reachable via ``pd.status_bar``. The public push-side surface is
    :meth:`ralph.display.parallel_display.ParallelDisplay.update_status_bar`
    (callers invoke ``display.update_status_bar(model)``); ``StatusBar.update(model)``
    is the *internal storage seam* the public method forwards into so the
    Live region picks the model up on its next refresh tick. The ``start()``
    and ``stop()`` methods are wired through ParallelDisplay's own
    ``start()`` / ``stop()`` lifecycle. Reads happen via ``last_model``.

    Attributes:
        _display: Same-package reference to the owning ParallelDisplay instance.
            Reads ``display._ctx`` (live DisplayContext that the runner keeps
            fresh via SIGWINCH / poll refreshers) and ``display._is_quiet``.
        _home: Home directory resolved once at construction; passed to
            ``render_status_bar`` so render stays pure.
        _model: Last model supplied via :meth:`update`; ``None`` until first update.
        _live: Lazily-constructed ``rich.live.Live`` instance (or ``None``).
        _lock: Threading lock guarding ``_model`` assignment.
    """

    __slots__ = ("_display", "_fallback_rendered", "_home", "_live", "_lock", "_model")

    def __init__(self, display: ParallelDisplay) -> None:
        self._display: _StatusBarHost = display
        self._home = str(pathlib.Path.home())
        self._model: StatusBarModel | None = None
        self._live: _Live | None = None
        self._lock = threading.Lock()
        self._fallback_rendered = False

    @property
    def is_active(self) -> bool:
        """Return True when a Live region is currently active for this StatusBar."""
        return self._live is not None

    @property
    def last_model(self) -> StatusBarModel | None:
        """Return the most recent :class:`StatusBarModel` supplied via :meth:`update`."""
        return self._model

    def _ctx(self) -> DisplayContext:
        """Return the live DisplayContext from the owning display (refreshed by SIGWINCH/poll)."""
        return self._display._ctx

    def _real_tty(self) -> bool:
        """Return True only when the console is a real TTY (not force_terminal+StringIO)."""
        ctx = self._ctx()
        console = ctx.console
        is_terminal_attr: bool | None = getattr(console, "is_terminal", None)
        if not is_terminal_attr:
            return False
        file_obj: IO[str] = console.file
        # ``IO[str]`` declares ``isatty() -> bool`` so the call site is
        # type-safe end-to-end without a suppression. ``Rich.console`` types
        # ``file`` as ``IO[str] | None``; we assume non-None here because
        # ``Rich.make_console`` always sets a real file. This is the same
        # narrowing ``Rich.Console.is_terminal`` itself uses.
        isatty_result: bool = file_obj.isatty()
        return is_terminal_attr and isatty_result

    def _gate(self) -> bool:
        """Return True when :meth:`start` should construct the Live region."""
        if self._live is not None:
            return False
        if bool(self._display._is_quiet):
            return False
        return self._real_tty()

    def _renderable(self) -> Text:
        """Return the current renderable for the Live region's get_renderable callable."""
        model = self._model
        if model is None:
            return Text(" ")
        return render_status_bar(model, self._ctx(), home=self._home)

    def _live_console_is_interactive(self) -> bool:
        is_interactive: object = getattr(self._ctx().console, "is_interactive", False)
        return is_interactive is True

    def _fallback_render_once(self) -> None:
        if self._live_console_is_interactive():
            return
        self._fallback_cleanup()
        self._ctx().console.print(self._renderable())
        self._fallback_rendered = True

    def _fallback_cleanup(self) -> None:
        if not self._fallback_rendered:
            return
        self._fallback_rendered = False
        file_obj: IO[str] = self._ctx().console.file
        file_obj.write("\r\x1b[1A\x1b[2K")
        file_obj.flush()

    def start(self) -> None:
        """Begin rendering the Status Bar inside a transient Rich Live region.

        No-op when the real-TTY gate is closed (non-tty console, redirected
        output, StringIO test console, quiet mode), or when a Live region
        is already active. Idempotent.

        The Live region is constructed with ``get_renderable=self._renderable``
        so each refresh tick re-reads the latest model — the initial
        ``renderable`` argument is only the first-frame content.

        Correctness: ``_live`` is committed to ``self._live`` ONLY after
        ``Live.start()`` succeeds. If ``Live.start()`` raises (e.g. on a
        console whose ``Live.start()`` path is broken, or a parent that
        suppresses the underlying terminal), the exception is swallowed
        but ``self._live`` stays ``None``. This keeps ``is_active`` honest
        (``is_active`` is defined as ``self._live is not None``) so a
        later ``start()`` retry still succeeds and ``stop()`` on an
        unstarted bar remains a no-op.
        """
        if not self._gate():
            return
        with contextlib.suppress(Exception):
            from rich.live import Live

            live = Live(
                self._renderable(),
                console=self._ctx().console,
                transient=_STATUS_BAR_TRANSIENT,
                refresh_per_second=_STATUS_BAR_REFRESH_PER_SECOND,
                screen=False,
                get_renderable=self._renderable,
            )
            live.start()
            self._live = live
            self._fallback_render_once()

    def stop(self) -> None:
        """Tear down the Live region. Idempotent and safe to call without :meth:`start`."""
        live = self._live
        if live is None:
            return
        self._live = None
        with contextlib.suppress(Exception):
            live.stop()
        with contextlib.suppress(Exception):
            self._fallback_cleanup()

    def update(self, model: StatusBarModel) -> None:
        """Store ``model`` for the Live region to pick up on its next refresh tick.

        This is the internal storage seam the public push-side surface
        :meth:`ralph.display.parallel_display.ParallelDisplay.update_status_bar`
        forwards into. Callers should NOT invoke ``status_bar.update(model)``
        directly; the consolidated contract is ``display.update_status_bar(model)``.

        On interactive consoles the update is intentionally a pure store:
        it does NOT force an immediate ``live.refresh()``. The persistent
        footer is owned by the Live region's
        :data:`_STATUS_BAR_REFRESH_PER_SECOND` cadence (4.0 Hz / 250 ms by
        default), so update calls feed a fresh :class:`StatusBarModel` and
        the next refresh tick renders it. On Rich "dumb terminal" consoles
        where ``Live.start()`` succeeds but Rich refuses to draw frames, the
        fallback renderer erases the previous fallback row and emits one
        bounded replacement row so ``is_active`` stays observable.

        Safe to call before :meth:`start`; in that case the model is
        stored and the subsequent :meth:`start` constructs the Live region
        using the latest model as its initial renderable. Thread-safe
        under :attr:`_lock`.
        """
        with self._lock:
            self._model = model
        if self._live is not None:
            with contextlib.suppress(Exception):
                self._fallback_render_once()
