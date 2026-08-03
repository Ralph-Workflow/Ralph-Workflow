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
  can accommodate it),
- elapsed run time and current agent identity when the path budget has room.

Width-driven degradation (in order) so ``cell_len(text.plain) <= ctx.width``
holds at every width:

1. Path middle-truncation absorbs excess length on long paths.
2. Phase label tail-truncation absorbs excess length on long labels.
3. Iteration label form degrades canonical -> compact -> minimal.
4. Phase marker is dropped below the marker-fit threshold.
5. Per-iteration glyphs are dropped below the glyph-fit threshold.
6. Elapsed-time and agent-identity segments drop before iteration context
   when the path budget cannot preserve both core fields.
7. Iteration segments drop one at a time (outer_dev first, then
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

- ``_STATUS_BAR_REFRESH_PER_SECOND`` (default ``4.0``): bounded Live-region
  repaint cadence. The elapsed clock changes each second while a run is active;
  unchanged frames are not re-emitted by the non-interactive fallback.
- ``_STATUS_BAR_TRANSIENT`` (default ``True``): frames are erased on stop,
  preserving clean scrollback, copy/paste, terminal search, and post-run log
  review. The completion panel carries the durable outcome; the final
  terminated-state push is visible before teardown.

Default rendering
-----------------

The single default layout renders (in priority order)::

    [attention] [integration_alert] [phase_marker] {phase_label}
                [milestone] {liveness} {elapsed}
                [milestone] {outer_dev} Cycle N/cap
                [milestone] {inner_analysis} iter N/cap
                [milestone] Agent name [milestone] {workspace_root}

A field is omitted entirely (no ``--`` placeholder) when its iteration
field is ``None`` on the model. The phase marker glyph is omitted when
``ctx.glyphs_enabled`` is ``False`` so ASCII consoles render a clean
prefix.
"""

from __future__ import annotations

import contextlib
import dataclasses
import os
import pathlib
import re
import threading
import time
from dataclasses import dataclass
from typing import IO, TYPE_CHECKING, Protocol

from rich.cells import cell_len
from rich.text import Text

from ralph.display.phase_status import (
    format_analysis_cycle,
    format_analysis_cycle_compact,
    format_analysis_cycle_minimal,
    format_dev_cycle,
    format_dev_cycle_compact,
    format_dev_cycle_minimal,
)
from ralph.display.theme import (
    _DISPLAY_IDENTITY_ACTIVE_SET,
    _fresh_style,
    identity_color,
    pick_status_styles,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from rich.live import Live as _Live

    from ralph.display.context import DisplayContext
    from ralph.display.parallel_display import ParallelDisplay

    class _StatusBarHost(Protocol):
        """Structural type for the ``display`` reference StatusBar composes against."""

        _ctx: DisplayContext
        _is_quiet: bool

        @property
        def watchdog_attention(self) -> str | None:
            """Watchdog-sourced attention state (the STALLED slot).

            wt-047-stall-label: the watchdog is the sole owner of the
            STALLED label. The Status Bar host reads this property on
            every Live tick and substitutes the value into the model
            ONLY when the pushed ``attention`` is None (a pushed
            ``waiting`` / ``retrying`` / ``terminated`` always wins).
            The property is missing-safe (``getattr`` default ``None``)
            so stub hosts degrade to the pushed model.
            """
            ...


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
#     into the bar. All later budgeting uses terminal display cells
#     (``rich.cells.cell_len``), not character count.
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
    required because tabs expand to the next terminal tab stop while
    all layout is measured with terminal display cells; without this
    normalization a single tab in a path or phase label would silently
    inflate the rendered width and break the
    ``cell_len(text.plain) <= ctx.width`` invariant the Live region is
    sized against. Leading / trailing whitespace is trimmed so a
    path that is otherwise non-empty cannot render as an invisible
    bar segment.
    """
    if not text:
        return ""
    cleaned = _SAFE_LINE_ESCAPE_RE.sub("", text)
    cleaned = _SAFE_LINE_CONTROL_RE.sub(" ", cleaned)
    cleaned = _SAFE_LINE_NEWLINE_RE.sub(" ", cleaned)
    return cleaned.strip()


# Canonical label widths (full form: ``Cycle 1/3`` / ``iter 2/5``).
# These reflect the WORST-CASE actual label length with multi-digit
# caps (e.g. ``Cycle 99/999`` is 12 chars; ``iter 99/999`` is 11
# chars). The budget allocator reserves exactly these widths, so the
# canonical form fits at the AC-03 canonical threshold (120 cols),
# where the label MUST render (only path/phase truncation adapts to
# width — the AC-03 invariant).
_OUTER_DEV_LABEL_MAX_CHARS: int = 11
_INNER_ANALYSIS_LABEL_MAX_CHARS: int = 11
# Compact label widths (C1/3 / i2/5).
_OUTER_DEV_LABEL_COMPACT_MAX_CHARS: int = 6
_INNER_ANALYSIS_LABEL_COMPACT_MAX_CHARS: int = 7
# Minimal label widths (1/3 / 2/5; no prefix).
_OUTER_DEV_LABEL_MINIMAL_MAX_CHARS: int = 5
_INNER_ANALYSIS_LABEL_MINIMAL_MAX_CHARS: int = 6
# Maximum width of the ``N/cap`` / ``#N`` suffix carried by
# ``format_dev_cycle`` (worst case ``99/999`` = 7 chars). Used by
# :func:`_outer_label_canonical_chars` to size the canonical-form
# budget when the caller supplies a custom ``outer_label``
# (e.g. ``Remediation`` -> ``Remediation 99/999`` = 18 chars). The
# canonical-form branch in :func:`_format_dev_label` gates on this
# so a wide custom label renders in full rather than being clipped by
# the terminal-width clamp that downstream truncates the rendered line.
_OUTER_DEV_LABEL_SUFFIX_MAX_CHARS: int = 7
# Threshold at and above which the canonical (full) label form is
# always honored regardless of how much phase/path truncation is
# needed. Below this threshold the implementation may degrade to
# compact/minimal forms when canonical labels cannot fit alongside
# phase + path at the terminal width.
_CANONICAL_FIT_THRESHOLD: int = 120
_AGENT_FIT_THRESHOLD: int = 60
_AGENT_PATH_FIT_THRESHOLD: int = 80
_NARROW_AGENT_PATH_LAYOUT_MAX_WIDTH: int = 85
_AGENT_LABEL_FIXED_WIDTH: int = len("Agent claude")
_ELAPSED_SHORT_FIXED_WIDTH: int = 6

# The 60-col rung drops only the path. The agent remains visible at 60,
# then yields at the 40-col floor with the rest of the optional context.
_PATH_DROP_THRESHOLD: int = 60

# DA-003 (wt-028-display AC-02): the 80-col rung is the spec's
# boundary between path-elided (above) and phase-abbreviated (below)
# layouts. Below this width the phase label is tail-truncated to
# the abbreviated form so the dropped path's budget can be
# redirected to recognisable segments.
_PHASE_ABBREVIATE_THRESHOLD: int = 80

# Narrow bars need distinct phase carriers, not a shared ``Dev`` / ``Pla``
# prefix. Values stay unique within the three-cell floor budget.
_PHASE_ABBREVIATIONS: dict[str, str] = {
    "planning": "Plan",
    "planning_analysis": "PAn",
    "development": "Dev",
    "development_commit_cleanup": "DCl",
    "development_commit": "DCm",
    "development_analysis": "DAn",
    "development_final_commit_cleanup": "FCl",
    "development_final_commit": "FCm",
    "complete": "DONE",
    "failed_terminal": "FAIL",
    "cancelled": "CXL",
}


def _abbreviated_phase_label(label: str, budget: int, width: int) -> str:
    """Return a readable phase carrier unique at every abbreviated width."""
    key = label.lower().replace(" ", "_")
    return _tail_truncate(_PHASE_ABBREVIATIONS.get(key, key.title()), budget)


# wt-028-display S-2/S-3 (AC-01/AC-02): the 40-col rung is the spec's
# floor below which the liveness glyph + elapsed segment drop
# entirely. At every width >= 40 the liveness glyph and the elapsed
# short form survive (spec: "At 40 -- the floor -- attention,
# phase, liveness, position, and elapsed survive"). The constant
# lands every ``if ctx.width >= 40`` check in this module on the
# same spec source.
_FLOOR_THRESHOLD: int = 40

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
_MIN_PHASE_BUDGET: int = 3
_MIN_PATH_BUDGET: int = 6
_MIN_PHASE_PLUS_PATH: int = _MIN_PHASE_BUDGET + _MIN_PATH_BUDGET


# P0 (wt-028-display AC-03): attention-state presentation. The label
# + glyph + style trio means color is never the only carrier of the
# ``needs you`` signal -- an operator who cannot distinguish the
# hues still reads the bare label and glyph. The glyph space is the
# same one every Status Bar glyph already pulls from, so ASCII
# consoles degrade the glyph to ``*`` automatically via
# :meth:`DisplayContext.glyph_for`.
ATTENTION_PRESENTATION: dict[str, tuple[str, str, str]] = {
    "starting": ("STARTING", "liveness", "theme.status.info"),
    "waiting": ("WAITING", "waiting", "theme.status.warn"),
    "stalled": ("STALLED", "stalled", "theme.status.error"),
    "retrying": ("RETRYING", "retrying", "theme.status.warn"),
    "terminated": ("DONE", "terminated", "theme.status.info"),
    "completed": ("COMPLETE", "terminated", "theme.status.info"),
    "failed": ("FAILED", "terminated", "theme.status.error"),
    "cancelled": ("CANCELLED", "terminated", "theme.status.warn"),
}


# DA-001 (wt-028-display AC-01): worst-case attention slot width.
# The attention slot is reserved at every width so its arrival (or
# clearing) never shifts any neighbour. Its label width is measured in
# terminal cells; glyph widths are resolved from the active context below.
_ATTENTION_PRESENTATION_MAX_LABEL_CELLS: int = max(
    cell_len(label) for label, _glyph, _style in ATTENTION_PRESENTATION.values()
)


def _attention_slot_reserved_width(ctx: DisplayContext) -> int:
    """Return the reserved width of the attention slot for ``ctx``.

    The attention slot is reserved at every width so a healthy
    run's blank space yields the same byte position as a populated
    ``WAITING`` / ``STALLED`` / ``RETRYING`` / ``DONE`` label. The
    returned width is the worst case across the four states (the
    longest label plus its glyph and one space) plus the trailing
    separator so the trailing separator lands at a stable position
    whether the slot is empty or populated. The function does no I/O
    and is safe to call per-render.

    DA-001 (wt-028-display AC-01).
    """
    separator = _field_separator(ctx)
    max_glyph_len = max(
        (
            cell_len(ctx.glyph_for(glyph_key))
            for glyph_key in (
                glyph_key for _label, glyph_key, _style in ATTENTION_PRESENTATION.values()
            )
        ),
        default=0,
    )
    # ``{glyph} {label}{separator}`` is the rendered string when populated.
    return max_glyph_len + 1 + _ATTENTION_PRESENTATION_MAX_LABEL_CELLS + cell_len(separator)


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
        elapsed_seconds: Elapsed run duration supplied by the display clock,
            never persisted in pipeline state.
        agent_name: Current agent identity supplied by the phase-entry model.
        outer_label: Optional phase-appropriate label for the outer cycle
            (``None`` -> the neutral ``Cycle`` label via
            :func:`ralph.display.phase_status.format_dev_cycle`).
            Set to ``Remediation`` / ``Round`` etc. by callers whose
            phase-level semantics want a different noun than the default
            neutral label, so the bar never claims a phase is something
            it isn't (AC-02). When ``None`` the bar renders ``Cycle N/cap``;
            when set, it renders ``<outer_label> N/cap``.
        attention: ``None`` (healthy), ``"waiting"``, ``"stalled"``,
            ``"retrying"``, or ``"terminated"``. P0 (wt-028-display AC-03).
            The ``"stalled"`` value is sourced EXCLUSIVELY from the
            idle watchdog via the host's ``watchdog_attention`` slot
            (wt-047-stall-label): the model carries the pushed
            operator-state value, and the Status Bar host substitutes
            the watchdog-sourced value on each Live tick ONLY when the
            pushed ``attention`` is None. Pushed operator states
            (``waiting`` / ``retrying`` / ``terminated``) always win.
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
    elapsed_seconds: float | None = None
    agent_name: str | None = None
    # P0 (wt-028-display AC-01): the run-start monotonic anchor lets
    # ``render_status_bar`` recompute elapsed at render time so the
    # bar keeps ticking during quiet agent turns without a model
    # re-push. ``None`` keeps the existing snapshot-elapsed contract.
    run_started_monotonic: float | None = None
    # P0 (wt-028-display AC-02 / AC-03): the typed attention slot is
    # the ONLY place the STALLED label is sourced from in the
    # pushed model. wt-047-stall-label: a separate 30s-gap derivation
    # was removed (zero dead code); the watchdog is the sole owner of
    # the STALLED label and surfaces its state via the host's
    # ``watchdog_attention`` property (``_model_with_live_attention``).
    attention: str | None = None


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
        return _HOME_PREFIX + path[len(home_str) :]
    return path


def _slice_to_cells(text: str, budget: int) -> str:
    """Return the longest prefix of ``text`` that fits in ``budget`` cells."""
    if budget <= 0:
        return ""
    result: list[str] = []
    used = 0
    for char in text:
        width = cell_len(char)
        if used + width > budget:
            break
        result.append(char)
        used += width
    return "".join(result)


def _middle_truncate_path(path: str, budget: int) -> str:
    """Return ``path`` truncated to at most ``budget`` terminal cells via middle ellipsis."""
    if cell_len(path) <= budget:
        return path
    last_sep = path.rfind(os.sep)
    last_segment = path[last_sep + 1 :] if last_sep >= 0 else path
    separator_budget = cell_len(_ELLIPSIS) + 1
    if budget >= cell_len(last_segment) + separator_budget:
        return f".../{last_segment}"
    return _tail_truncate(last_segment, budget)


def _tail_truncate(text: str, budget: int) -> str:
    """Return ``text`` tail-truncated to ``budget`` terminal cells ending with ``...``."""
    if cell_len(text) <= budget:
        return text
    ellipsis_width = cell_len(_ELLIPSIS)
    if budget <= ellipsis_width:
        return _slice_to_cells(text, budget)
    return _slice_to_cells(text, budget - ellipsis_width).rstrip() + _ELLIPSIS


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
    return cell_len(separator) + cell_len(glyph) + 1 + label_max_chars


@dataclass(frozen=True)
class _FieldBudgets:
    """Width-aware rendering budgets derived from ``ctx.width``.

    The single default-mode Status Bar renders phase + dir + (any
    applicable outer_dev) + (any applicable inner_analysis) at every
    width where the iteration segments fit.

    AC-03 invariant: at widths >= ``_CANONICAL_FIT_THRESHOLD`` (120
    cols) the iteration label form is ALWAYS the canonical
    (``Cycle 1/3`` / ``iter 2/5``) form regardless of how much
    phase/path truncation is needed. Only path middle-truncation and
    phase tail-truncation budgets adapt to width at those widths.

    Below ``_CANONICAL_FIT_THRESHOLD`` the implementation may degrade
    to compact (``C1/3`` / ``i2/5``) or minimal (``1/3`` / ``2/5``)
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
    canonical ``■ Cycle 1/3 ◆ ◎ iter 2/5`` at 100+ cols.
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
    has_agent: bool = False,
    outer_label_canonical_chars: int = _OUTER_DEV_LABEL_MAX_CHARS,
) -> _FieldBudgets:
    """Derive width-aware budgets that always fit ``ctx.width``.

    AC-03 invariant: at widths >= ``_CANONICAL_FIT_THRESHOLD`` (120 cols)
    the iteration label form is ALWAYS canonical (``Cycle N/cap`` /
    ``iter N/cap``); only path middle-truncation and phase
    tail-truncation budgets adapt to width. Below the threshold the
    implementation may degrade to compact (``C1/3`` / ``i2/5``) or
    minimal (``1/3`` / ``2/5``) forms to fit the bar at very narrow
    widths.

    ``outer_label_canonical_chars`` overrides the default canonical-form
    width when the caller supplies a custom ``outer_label`` (e.g.
    ``Remediation``). The default ``_OUTER_DEV_LABEL_MAX_CHARS`` (10)
    accommodates ``Cycle 99/999`` but a custom label like ``Remediation
    2/3`` needs more room -- the override widens the canonical-form
    budget so a wide custom label renders in full rather than being
    clipped by the terminal-width clamp downstream. Callers compute the
    override via :func:`_outer_label_canonical_chars`.

    Iteration segments are present (in canonical / compact / minimal
    form) when the model fields are non-``None`` and ``ctx.width``
    can accommodate them. The function picks the most descriptive
    layout that fits ``ctx.width``:

    1. At widths ``>= _CANONICAL_FIT_THRESHOLD`` the canonical
       ``Cycle N/cap`` / ``iter N/cap`` labels ALWAYS render in full
       with the marker and per-iteration glyphs (phase/path truncate
       to absorb any remaining width pressure).
    2. Below the canonical-fit threshold, the compact form
       (``C1/3`` / ``i2/5``) is used when it fits.
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
    separator_len = cell_len(_field_separator(ctx))
    marker_len = cell_len(ctx.glyph_for("phase_marker") + " ") if ctx.glyphs_enabled else 0
    outer_dev_glyph_len = cell_len(ctx.glyph_for("outer_dev"))
    inner_analysis_glyph_len = cell_len(ctx.glyph_for("inner_analysis"))

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
        """Total chrome excluding phase + path: marker + iter segments."""
        ml = marker_len if with_marker else 0
        return ml + _iter_width(
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
        # DA-001 (wt-028-display AC-01): subtract the attention slot
        # reserved width from the available budget so the budget
        # allocator hands out path_budget / phase_budget that account
        # for the leading reserved slot. The reservation is constant
        # per-width (worst-case across the four attention states), so
        # it lands cleanly in the chrome sum rather than the per-iter
        # allocation.
        attention_chrome = _attention_slot_reserved_width(ctx)
        # DA-002 (wt-028-display AC-01) / S-2 (S-3): subtract the
        # elapsed display chrome when the bar is wide enough to
        # render it. The renderer now keeps the elapsed segment at
        # every width >= 40 (the spec floor) and drops it below 40.
        # Above 60 the wide form (Time H:MM:SS, 13 chars) is used;
        # at 40-59 the short form (XmXXs / XhXXm, 5 chars) is used.
        if ctx.width >= _CANONICAL_FIT_THRESHOLD:
            elapsed_chrome = _ELAPSED_FIXED_WIDTH + separator_len
        elif ctx.width >= _FLOOR_THRESHOLD:
            elapsed_chrome = _ELAPSED_SHORT_FIXED_WIDTH + separator_len
        else:
            elapsed_chrome = 0
        agent_chrome = (
            separator_len
            + (
                _AGENT_LABEL_FIXED_WIDTH
                if ctx.width < _CANONICAL_FIT_THRESHOLD
                else len("Agent pi · minimax/MiniMax-3")
            )
            if has_agent and ctx.width >= _AGENT_FIT_THRESHOLD
            else 0
        )
        path_chrome = separator_len if ctx.width > _PATH_DROP_THRESHOLD else 0
        available = (
            ctx.width
            - _chrome(
                outer_label,
                inner_label,
                with_marker,
                with_glyph,
                include_outer=include_outer,
                include_inner=include_inner,
            )
            - attention_chrome
            - elapsed_chrome
            - agent_chrome
            - path_chrome
        )
        if available < _MIN_PHASE_PLUS_PATH:
            return None
        # Allocate remaining space to phase + path. Phase gets up to
        # DEFAULT_PHASE_LABEL_BUDGET chars (tail-truncated by the
        # caller); anything beyond that goes to path. When the bar
        # cannot afford the default phase cap, phase gets whatever
        # remains after reserving the path minimum so the workspace
        # path stays readable per AC-07.
        if ctx.width < _PHASE_ABBREVIATE_THRESHOLD:
            phase_budget = _MIN_PHASE_BUDGET
            path_budget = available - phase_budget
        elif available - _MIN_PATH_BUDGET >= DEFAULT_PHASE_LABEL_BUDGET:
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
        if ctx.width < _PHASE_ABBREVIATE_THRESHOLD:
            phase_budget = min(phase_budget, _MIN_PHASE_BUDGET)
            path_budget = available - phase_budget
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
        ((outer_label_canonical_chars, _INNER_ANALYSIS_LABEL_MAX_CHARS),)
        if ctx.width >= _CANONICAL_FIT_THRESHOLD
        else (
            (_OUTER_DEV_LABEL_COMPACT_MAX_CHARS, _INNER_ANALYSIS_LABEL_COMPACT_MAX_CHARS),
            (_OUTER_DEV_LABEL_MINIMAL_MAX_CHARS, _INNER_ANALYSIS_LABEL_MINIMAL_MAX_CHARS),
        )
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
            for with_marker in (
                (False,)
                if _AGENT_PATH_FIT_THRESHOLD <= ctx.width <= _NARROW_AGENT_PATH_LAYOUT_MAX_WIDTH
                else (True, False)
            ):
                for with_glyph in (
                    (False, True)
                    if _AGENT_PATH_FIT_THRESHOLD <= ctx.width <= _NARROW_AGENT_PATH_LAYOUT_MAX_WIDTH
                    else (True, False)
                ):
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


def _pad_to_cells(text: str, width: int) -> str:
    """Pad ``text`` to an exact terminal-cell width without misplacing wide glyphs."""
    return text + " " * max(0, width - cell_len(text))


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

    The returned label NEVER exceeds ``max_chars`` so the bar's
    downstream width clamp (which truncates the whole rendered line)
    cannot silently drop the iteration value or cap. When
    ``outer_label`` is wider than the canonical budget, the caller is
    expected to have allocated a wider budget via
    :func:`_outer_label_canonical_chars` -- if ``max_chars`` is still
    too small for the canonical form, the function degrades to the
    compact form (with a per-call substitution) and finally the
    minimal form so the cap is preserved at every width.
    """
    if max_chars <= 0:
        return ""
    canonical_needed = _outer_label_canonical_chars(outer_label)
    if max_chars >= canonical_needed:
        label = format_dev_cycle(n, cap)
        if outer_label:
            # replace the default 'Cycle' / 'N' label with the
            # caller's noun. Safe because format_dev_cycle produces
            # ``Cycle N/cap`` / ``Cycle #N`` -- both start with 'Cycle '.
            rendered = f"{outer_label} {label.split(' ', 1)[1]}"
            return _pad_to_cells(_tail_truncate(rendered, max_chars), max_chars)
        return _pad_to_cells(label, max_chars)
    if max_chars >= _OUTER_DEV_LABEL_COMPACT_MAX_CHARS:
        label = format_dev_cycle_compact(n, cap)
        if outer_label:
            # Compact form is 'C{n}/{cap}' / 'C#{n}' -- swap the 'C'
            # prefix for the first char of the supplied noun when it is
            # exactly one character long; otherwise fall back to the
            # neutral compact label (avoid truncation hazards at this
            # tight 4-char budget).
            initial = outer_label[:1]
            rendered = f"{initial}{label[1:]}"
            return _pad_to_cells(_tail_truncate(rendered, max_chars), max_chars)
        return _pad_to_cells(label, max_chars)
    # Minimal form has no prefix to swap -- at this tight budget the
    # canonical cap-bearing string must be preserved so the operator
    # still sees ``N/cap``; truncate defensively in case ``max_chars``
    # is below the minimal form's width.
    minimal = format_dev_cycle_minimal(n, cap)
    return _pad_to_cells(_tail_truncate(minimal, max_chars), max_chars)


def _outer_label_canonical_chars(outer_label: str | None) -> int:
    """Return the canonical-form width the Status Bar needs for the outer label.

    The default ``Cycle`` label is bounded by
    :data:`_OUTER_DEV_LABEL_MAX_CHARS` (12 chars; ``Cycle 99/999`` /
    ``Cycle #N`` worst case). When the caller supplies a custom
    ``outer_label`` (e.g. ``Remediation`` / ``Round``), the canonical
    form is ``<outer_label> <suffix>`` where ``suffix`` is the same
    ``N/cap`` / ``#N`` text the neutral label carries. The custom
    label's canonical width is therefore ``len(outer_label) + 1 + len(suffix)``.

    ``_format_dev_label`` uses this value to gate the canonical-form
    branch so a wide custom label (e.g. ``Remediation 2/3`` = 15 chars)
    always renders in full rather than being clipped by
    :func:`render_status_bar`'s terminal-width clamp. ``_format_dev_label``
    also clamps its returned string to ``max_chars`` as a defence so a
    mis-allocated budget cannot drop the value or cap.
    """
    suffix_max = _OUTER_DEV_LABEL_SUFFIX_MAX_CHARS
    if outer_label is None:
        return _OUTER_DEV_LABEL_MAX_CHARS
    return cell_len(_safe_single_line(outer_label)) + 1 + suffix_max


def _format_elapsed(seconds: float) -> str:
    """Return a compact, label-carrying elapsed-time segment value."""
    total = max(0, int(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"Time {hours}:{minutes:02d}:{secs:02d}"
    return f"Time {minutes:02d}:{secs:02d}"


# DA-002 (wt-028-display AC-01): worst-case fixed width for the
# elapsed label. The elapsed segment crosses three format
# boundaries (``Time mm:ss`` -> ``Time H:mm:ss`` -> ``Time HH:mm:ss``)
# when the hour count rolls from 0 to 1 to 2 digits. Without a
# fixed width, those boundaries push the workspace-path byte
# position sideways at wider widths. We pad to the worst case
# (``Time 99:59:59`` = 13 chars) so neighbouring segments stay
# byte-stable across the elapsed format roll-overs.
_ELAPSED_FIXED_WIDTH: int = 13
# DA-002 pin: ``Time 99:59:59`` MUST equal ``_ELAPSED_FIXED_WIDTH``.
assert len("Time 99:59:59") == _ELAPSED_FIXED_WIDTH, (
    f"_ELAPSED_FIXED_WIDTH must match the worst-case realistic label width; "
    f"got {_ELAPSED_FIXED_WIDTH} but 'Time 99:59:59' is {len('Time 99:59:59')} chars"
)


def _format_elapsed_fixed(seconds: float | None) -> str:
    """Return the elapsed label padded to the fixed-width column.

    DA-002 (wt-028-display AC-01). Returns an all-blank column of
    the same width when ``seconds`` is ``None`` so the segment is
    reserved in the bar even when the caller has not yet computed
    elapsed (the Live region picks up the value on the next
    refresh tick).
    """
    if seconds is None:
        return " " * _ELAPSED_FIXED_WIDTH
    return _format_elapsed(seconds).ljust(_ELAPSED_FIXED_WIDTH)


def _resolve_elapsed_seconds(model: StatusBarModel, now_monotonic: float | None) -> float | None:
    """Return the recomputed elapsed seconds, or the snapshot fallback.

    wt-028-display S-2: extract the recompute logic from
    ``_resolve_elapsed_label`` so the fixed-width column can
    format the recomputed numeric value (in seconds) rather
    than the formatted string. The label-string variant is
    retained for any caller that still wants the pre-formatted
    label.
    """
    if (
        model.run_started_monotonic is not None
        and now_monotonic is not None
        and now_monotonic >= model.run_started_monotonic
    ):
        return now_monotonic - model.run_started_monotonic
    return model.elapsed_seconds


def _liveness_frame(ctx: DisplayContext, now_monotonic: float | None) -> str:
    """Return a one-cell activity frame derived from the injected render clock."""
    if now_monotonic is None:
        return ctx.glyph_for("liveness")
    frames = ("⠋", "⠙", "⠹", "⠸") if ctx.glyphs_enabled else ("-", "\\", "|", "/")
    return frames[int(now_monotonic) % len(frames)]


def _format_elapsed_short(seconds: float | None) -> str:
    """Return a compact elapsed label that fits at the 40-col floor.

    wt-028-display S-2 (AC-01): the spec 40-col rung keeps the
    elapsed segment in a short form (12m41s / 1h02m) so the
    bar stays first-class at the floor size.
    """
    if seconds is None or seconds < 0:
        return " " * _ELAPSED_SHORT_FIXED_WIDTH
    total = int(seconds)
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h{minutes:02d}m".ljust(_ELAPSED_SHORT_FIXED_WIDTH)
    return f"{minutes}m{secs:02d}s".ljust(_ELAPSED_SHORT_FIXED_WIDTH)


def _split_agent_label(label: str) -> tuple[str, str]:
    """Split ``"Agent <name>"`` into the ``"Agent "`` prefix and the name.

    Used by :func:`render_status_bar` to color the agent's name
    portion with the deterministic identity color from
    :func:`ralph.display.theme.identity_color` while leaving the
    ``"Agent "`` carrier in the default status-info style. Returns
    ``("", label)`` when the label does not match the canonical
    shape so the caller falls back to the default rendering.
    """
    prefix = "Agent "
    if label.startswith(prefix):
        return prefix, label[len(prefix) :]
    return "", label


def _resolve_elapsed_label(model: StatusBarModel, now_monotonic: float | None) -> str:
    """Return the elapsed-time label, recomputed at render time when anchors are set.

    P0 (wt-028-display AC-01): when both ``run_started_monotonic`` and
    ``now_monotonic`` are present, recompute elapsed at render time so
    the bar keeps ticking during quiet agent turns without a model
    re-push. The fallback to the snapshot value keeps the existing
    pre-P0 contract for callers that supply only ``elapsed_seconds``.
    """
    base = _format_elapsed(model.elapsed_seconds) if model.elapsed_seconds is not None else ""
    if (
        model.run_started_monotonic is not None
        and now_monotonic is not None
        and now_monotonic >= model.run_started_monotonic
    ):
        return _format_elapsed(now_monotonic - model.run_started_monotonic)
    return base


def _prune_optional_segments(
    candidates: tuple[str, ...], separator: str, path_budget: int
) -> list[str]:
    """Drop optional segments (elapsed, agent) when they would crowd the path.

    Priority: keep all non-empty, then drop in order until the path
    has at least ``_MIN_PATH_BUDGET`` columns. The prune ladder is
    the canonical narrow-width contract: the workspace path stays
    readable at every width.

    wt-028-display S-2: ``elapsed`` is no longer a candidate here
    -- it is rendered in the fixed-width column and must never
    participate in the optional trailing pool. The signature now
    accepts a variable-length tuple so callers that pass only the
    agent label (or only a future optional) still typecheck.
    """
    keep: list[str] = [label for label in candidates if label]
    keep_width = sum(cell_len(separator) + cell_len(label) for label in keep)
    while keep and keep_width > path_budget - _MIN_PATH_BUDGET:
        # Drop the lowest-priority trailing segment.
        keep.pop()
        keep_width = sum(cell_len(separator) + cell_len(label) for label in keep)
    return keep


def _resolve_attention_state(model: StatusBarModel) -> str | None:
    """Return the active attention state for the bar.

    The ``attention`` slot in the pushed model is the SOLE source
    for ``stalled`` (wt-047-stall-label). The watchdog is the sole
    owner of the STALLED label and surfaces its state via the
    Status Bar host's ``watchdog_attention`` property (see
    :meth:`StatusBar._model_with_live_attention`); the renderer
    here never derives ``stalled`` from a time gap.

    Operator-pushed states (``waiting``, ``retrying``, ``terminated``)
    win over the watchdog-sourced ``stalled`` because they represent
    deliberate intent that the operator wants to see no matter what
    the watchdog assesses. When the pushed ``attention`` is ``None``,
    the host substitutes the watchdog-sourced value on the Live
    tick path; ``_resolve_attention_state`` reads the substituted
    value as ``model.attention`` and indexes it normally.

    Args:
        model: Status bar view-model carrying the attention slot
            (pushed operator state OR the host-substituted
            watchdog-sourced value).
    Returns:
        ``None`` (blank slot, healthy run) or one of the named
        attention values. The returned value is always a key of
        :data:`ATTENTION_PRESENTATION` so the renderer can index
        the presentation table without a defensive fallback.
    """
    pushed = model.attention
    if pushed is None:
        return None
    # Defensive: an unknown value is ignored so a future
    # addition to the named state set cannot poison the slot.
    if pushed in ATTENTION_PRESENTATION:
        return pushed
    return None


def _format_analysis_label(n: int, cap: int | None, max_chars: int) -> str:
    """Format the inner_analysis label using the form that fits ``max_chars``."""
    if max_chars <= 0:
        return ""
    if max_chars >= _INNER_ANALYSIS_LABEL_MAX_CHARS:
        return format_analysis_cycle(n, cap).ljust(max_chars)
    if max_chars >= _INNER_ANALYSIS_LABEL_COMPACT_MAX_CHARS:
        return format_analysis_cycle_compact(n, cap).ljust(max_chars)
    return format_analysis_cycle_minimal(n, cap).ljust(max_chars)


def _append_attention_slot(
    text: Text,
    model: StatusBarModel,
    ctx: DisplayContext,
    separator: str,
) -> None:
    """Render the reserved attention slot at the front of the bar.

    DA-001 (wt-028-display AC-01): the slot is RESERVED at every
    width -- a healthy run renders blank space and a populated
    ``WAITING`` / ``STALLED`` / ``RETRYING`` / ``DONE`` label
    renders at the same byte position. The reserved width is the
    worst case across the four states so the trailing separator
    lands at a byte-stable position whether the slot is empty or
    populated.
    """
    attention_state = _resolve_attention_state(model)
    attention_slot_width = _attention_slot_reserved_width(ctx)
    if attention_state is not None:
        label, glyph_key, style = ATTENTION_PRESENTATION[attention_state]
        glyph = ctx.glyph_for(glyph_key)
        status_name = {
            "theme.status.error": "error",
            "theme.status.warn": "warning",
            "theme.status.info": "info",
        }[style]
        text.append(
            f"{glyph} {label}",
            style=_fresh_style(pick_status_styles(ctx.terminal_background_is_light)[status_name][0]),
        )
        text.append(separator, style=ctx.theme.styles["theme.status.path_marker"])
        # Pad to the reserved width when the rendered state is
        # shorter than the worst case (e.g. ``DONE`` is shorter
        # than ``STALLED``) so the trailing separator lands at
        # the reserved position even on shorter states.
        rendered_so_far = cell_len(text.plain)
        if rendered_so_far < attention_slot_width:
            text.append(
                " " * (attention_slot_width - rendered_so_far),
                style="theme.status.path",
            )
    else:
        # Reserve the attention slot with blank space so
        # phase/path/cycle byte positions stay stable when
        # attention arrives. The slot width includes the trailing
        # separator so the phase segment begins at the reserved
        # position.
        text.append(
            " " * attention_slot_width,
            style="theme.status.path",
        )


def render_status_bar(
    model: StatusBarModel,
    ctx: DisplayContext,
    *,
    home: str | None = None,
    now_monotonic: float | None = None,
) -> Text:
    """Render the single-line Status Bar footer for the given model.

    This function is PURE: no I/O, no env reads, no Console construction,
    no ``Path.home()`` calls. ``home`` is a parameter so callers can
    supply the resolved home directory once (the ``StatusBar`` lifecycle
    resolves it at construction; tests pass an explicit value).

    The single default-mode layout renders attention, phase, liveness,
    elapsed time, applicable iteration context, agent identity, and finally
    the working directory in priority order. When ``ctx.width`` is too
    narrow to fit the canonical forms (``Cycle 1/3`` / ``iter 2/5``) the labels
    degrade through compact (``C1/3`` / ``i2/5``) and minimal
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
    outer_label = _safe_single_line(model.outer_label) if model.outer_label else None
    budgets = _field_overhead_and_label_budgets(
        ctx,
        has_outer_dev=has_outer_dev,
        has_inner_analysis=has_inner_analysis,
        has_agent=model.agent_name is not None,
        outer_label_canonical_chars=_outer_label_canonical_chars(outer_label),
    )

    # wt-028-display S-2 (AC-01): elapsed NO LONGER participates in
    # the optional trailing pool. The elapsed segment renders ONCE
    # in the fixed-width column (recomputed at render time via
    # ``_resolve_elapsed_seconds``).
    agent_label = f"Agent {_safe_single_line(model.agent_name)}" if model.agent_name else ""
    # Optional trailing context yields before path/phase/cycle context.
    # Reserve only path surplus so the established narrow-width
    # contract is unchanged.
    optional_segments = [agent_label] if agent_label and ctx.width >= _AGENT_FIT_THRESHOLD else []
    if ctx.width < _PHASE_ABBREVIATE_THRESHOLD:
        phase_display = _abbreviated_phase_label(phase_display, budgets.phase_budget, ctx.width)
    else:
        phase_display = _tail_truncate(phase_display, budgets.phase_budget)
    # Keep the source path intact until every preceding segment has rendered;
    # the final room calculation below then left-elides it to the actual budget.
    render_outer_dev = has_outer_dev and budgets.outer_dev_label_max_chars > 0
    render_inner_analysis = has_inner_analysis and budgets.inner_analysis_label_max_chars > 0
    text = Text()
    # DA-001 (wt-028-display AC-01): render the attention slot FIRST so
    # its arrival never shifts neighbours. The slot is RESERVED at
    # every width -- a healthy run renders blank space (the operator
    # learns the slot is empty by its blank state and gets a visible
    # label + glyph the moment the run is no longer healthy). The
    # reserved width is the worst case across the four attention
    # states so the trailing separator lands at a byte-stable position
    # whether the slot is empty or populated.
    _append_attention_slot(text, model, ctx, separator)
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
    # DA-003 (wt-028-display AC-02): abbreviate the phase label at the
    # 60-col rung when the path drops and the budget is tight. The
    # abbreviation uses a phase-specific short form
    # (``development`` -> ``dev``, ``planning`` -> ``plan``, etc.)
    # via :data:`_PHASE_ABBREVIATIONS`; an unknown phase falls back
    # to the first ``budget`` chars lowercased. The truncation
    # happens before the phase is appended so neighbouring segments
    # see the abbreviated form.
    text.append(phase_display, style=model.phase_style)
    # DA-002 (wt-028-display AC-01): render the elapsed segment with a
    # fixed-width buffer so neighbouring segments stay byte-stable
    # across the elapsed format roll-overs (mm:ss -> H:mm:ss ->
    # HH:mm:ss). The buffer is reserved whether or not the caller has
    # computed elapsed (the Live region picks the value up on its
    # next refresh tick). The segment is omitted at very narrow
    # widths (< 60) where the chrome for the other required
    # segments already saturates the bar -- the spec drops elapsed
    # below the supported floor (``At 40 -- the floor -- attention,
    # phase, liveness, position, and elapsed survive``; widths below
    # 40 are below the floor).
    # wt-028-display S-2/S-3 (AC-01/AC-02): render the liveness
    # glyph and the elapsed segment between phase and cycle.
    if ctx.width >= _FLOOR_THRESHOLD:
        # Liveness glyph sits BETWEEN phase and elapsed (spec
        # order: attention -> phase -> liveness -> elapsed ->
        # cycle -> iter -> agent -> cwd). Single-cell glyph
        # (byte-stable in width) so the Live tick can rotate
        # the frame without shifting neighbours.
        text.append(separator, style="theme.status.path_marker")
        liveness_glyph = _liveness_frame(ctx, now_monotonic)
        text.append(liveness_glyph, style="theme.status.info")
        if ctx.width >= _CANONICAL_FIT_THRESHOLD:
            text.append(
                _format_elapsed_fixed(_resolve_elapsed_seconds(model, now_monotonic)),
                style="theme.status.info",
            )
        else:
            text.append(
                _format_elapsed_short(_resolve_elapsed_seconds(model, now_monotonic)),
                style="theme.status.info",
            )
    if render_outer_dev:
        text.append(separator, style="theme.status.path_marker")
        if budgets.render_iter_glyph:
            text.append(ctx.glyph_for("outer_dev") + " ", style="theme.outer_dev")
        text.append(
            _format_dev_label(
                model.outer_dev_iteration or 0,
                model.outer_dev_cap,
                budgets.outer_dev_label_max_chars,
                outer_label=outer_label,
            ),
            style="theme.outer_dev",
        )
    if render_inner_analysis:
        text.append(separator, style="theme.status.path_marker")
        if budgets.render_iter_glyph:
            text.append(ctx.glyph_for("inner_analysis") + " ", style="theme.inner_analysis")
        text.append(
            _format_analysis_label(
                model.inner_analysis or 0,
                model.inner_analysis_cap,
                budgets.inner_analysis_label_max_chars,
            ),
            style="theme.inner_analysis",
        )
    for label in optional_segments:
        _append_optional_segment(text, label, model, separator, ctx)
    # CWD renders last, so measure its actual remaining room after every
    # higher-priority segment.  The allocator is deliberately conservative,
    # but this final measurement prevents the defensive whole-line clamp
    # from silently clipping a basename when its chrome estimate differs.
    path_room = ctx.width - cell_len(text.plain) - cell_len(separator)
    last_segment = path_display.rsplit(os.sep, 1)[-1]
    last_segment_width = cell_len(last_segment)
    path_display = (
        _middle_truncate_path(path_display, path_room)
        if path_room >= last_segment_width + cell_len(_ELLIPSIS) + 1
        # A final segment is still an honest, recognizable path when full
        # left elision cannot fit; omit it only when even that would clip.
        else last_segment
        if path_room >= last_segment_width
        else ""
    )
    _append_path_segment(text, path_display, separator, ctx.width)
    # Final width clamp is a defensive guard for widths below the supported
    # floor and hostile optional alerts; normal layouts fit by allocation.
    if ctx.width < 1:
        return Text(" ")
    if cell_len(text.plain) > ctx.width:
        text.truncate(ctx.width)
    return text


def _append_optional_segment(
    text: Text,
    label: str,
    model: StatusBarModel,
    separator: str,
    ctx: DisplayContext,
) -> None:
    """Render one optional agent segment after a leading separator.

    P3 (wt-028-display AC-15): the agent segment surfaces the
    deterministic identity color so the live footer reveals
    which agent produced the current cycle. The label is
    always preserved (color only assists recognition), so a
    grayscale / colourblind operator still reads the bare name.
    """
    text.append(separator, style="theme.status.path_marker")
    agent_width = (
        _AGENT_LABEL_FIXED_WIDTH
        if ctx.width < _CANONICAL_FIT_THRESHOLD
        else cell_len("Agent pi · minimax/MiniMax-3")
    )
    label = _pad_to_cells(_tail_truncate(label, agent_width), agent_width)
    if label.startswith("Agent ") and model.agent_name:
        agent_prefix, agent_rest = _split_agent_label(label)
        if agent_prefix:
            text.append(agent_prefix, style="theme.status.info")
        if agent_rest:
            text.append(
                agent_rest,
                style=identity_color(
                    model.agent_name,
                    active=(*_DISPLAY_IDENTITY_ACTIVE_SET, model.agent_name),
                    terminal_bg_is_light=ctx.terminal_background_is_light,
                ),
            )
    else:
        text.append(label, style="theme.status.info")


def _append_path_segment(
    text: Text,
    path_display: str,
    separator: str,
    width: int,
) -> None:
    """Render the trailing workspace path segment when the width budget allows.

    DA-004 (wt-028-display AC-02): cwd path renders LAST (after
    agent) so it is the trailing optional segment that elides /
    drops first at narrow widths. The path is elided from the
    left when tight (per spec) via the middle-truncate helper
    above. DA-003 (wt-028-display AC-02): at width < 60 the path
    drops entirely (no truncated ghost); the spec drops the path
    at the 60-col rung and abbreviates the phase label instead.
    """
    if width > _PATH_DROP_THRESHOLD and path_display:
        text.append(separator, style="theme.status.path_marker")
        text.append(path_display, style="theme.status.path")


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

    __slots__ = (
        "_clock",
        "_display",
        "_durable_frame",
        "_fallback_frame",
        "_fallback_rendered",
        "_home",
        "_last_live_frame",
        "_live",
        "_live_frame_rendered",
        "_lock",
        "_model",
        "_started",
        "_ticker",
        "_ticker_stop",
    )

    def __init__(
        self,
        display: ParallelDisplay,
        *,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._display: _StatusBarHost = display
        self._home = str(pathlib.Path.home())
        self._model: StatusBarModel | None = None
        self._live: _Live | None = None
        self._lock = threading.RLock()
        self._fallback_rendered = False
        self._fallback_frame: str | None = None
        self._started = False
        self._durable_frame: str | None = None
        self._last_live_frame: str | None = None
        self._live_frame_rendered = False
        self._ticker: threading.Thread | None = None
        self._ticker_stop: threading.Event | None = None
        # P0 (wt-028-display AC-01): injectable clock so the bar can
        # recompute elapsed on every Live tick without touching
        # ``time.monotonic`` directly (and so tests can drive the
        # tick deterministically with no ``time.sleep``).
        self._clock: Callable[[], float] = clock if clock is not None else time.monotonic

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
        """Return the current renderable for the Live region's get_renderable callable.

        P0 (wt-028-display AC-01): the Live region re-invokes
        ``get_renderable`` on every refresh tick. Passing the current
        ``time.monotonic`` anchor here means the elapsed segment
        recomputes on each tick (when ``model.run_started_monotonic``
        is set), so the bar keeps ticking during quiet agent turns
        without a model re-push.

        wt-047-stall-label (DA-001): the watchdog-sourced attention
        substitution fires BEFORE the anchor branch so the bar
        mirrors the watchdog's STALLED assessment even when no
        ``run_started_monotonic`` anchor was pushed (the previous
        ordering only substituted on the anchored branch and left
        the anchor-less host invisible to the watchdog signal).
        """
        model = self._model
        if model is None:
            return Text(" ")
        # Apply the watchdog-attention substitution FIRST so it is
        # honored regardless of whether the host pushed a
        # ``run_started_monotonic`` anchor. Only the elapsed-time
        # branch needs the live clock; the substitution itself is
        # anchor-agnostic.
        model = self._model_with_live_attention(model)
        if model.run_started_monotonic is None:
            return render_status_bar(model, self._ctx(), home=self._home)
        return render_status_bar(
            model,
            self._ctx(),
            home=self._home,
            now_monotonic=self._clock(),
        )

    def _model_with_live_attention(self, model: StatusBarModel) -> StatusBarModel:
        """Return ``model`` with the host's watchdog-sourced attention substituted in.

        wt-047-stall-label: the watchdog is the sole owner of the
        STALLED label. The Status Bar host publishes the
        watchdog-sourced attention via the ``watchdog_attention``
        property; this helper substitutes it into the model on every
        Live tick so the bar reflects the watchdog's assessment.

        Pushed operator states (``waiting`` / ``retrying`` /
        ``terminated``) ALWAYS win: a host that reports ``stalled``
        while the runner pushed ``waiting`` does not overwrite the
        pushed state. The substitution only fires when the pushed
        ``attention`` is ``None``.

        Defensive read via ``getattr``: a legacy or stub host without
        ``watchdog_attention`` (e.g. a unit-test double) degrades to
        the pushed model rather than raising inside the render
        callback and blanking the bar.
        """
        raw_attention: object = getattr(self._display, "watchdog_attention", None)
        if raw_attention is None:
            return model
        # Pushed states win.
        if model.attention is not None:
            return model
        # Defensive: unknown watchdog value must not poison the slot.
        if raw_attention not in ATTENTION_PRESENTATION:
            return model
        return dataclasses.replace(model, attention=raw_attention)

    def _live_console_is_interactive(self) -> bool:
        is_interactive: object = getattr(self._ctx().console, "is_interactive", False)
        return is_interactive is True

    def _fallback_render_once(self) -> None:
        if self._live_console_is_interactive():
            return
        renderable = self._renderable()
        if self._fallback_rendered and self._fallback_frame == renderable.plain:
            return
        self._fallback_cleanup()
        self._ctx().console.print(renderable)
        self._fallback_rendered = True
        self._fallback_frame = renderable.plain

    def _emit_durable_transition_if_changed(self) -> None:
        """Append a cold-readable footer transition when no live region is allowed.

        Redirected, piped, and CI destinations cannot safely repaint a footer.
        They instead receive one permanent line for each meaningful model change;
        the plain-text comparison suppresses repeated pushes while preserving
        phase, attention, elapsed, and identity transitions for later review.
        """
        # A force-terminal capture backed by StringIO is a renderer probe, not
        # a durable destination. It must not receive footer bytes: unlike a
        # real redirect it has no cold transcript contract and Rich reports it
        # as terminal-capable solely because the caller requested colour.
        if self._real_tty() or (self._started and self._ctx().console.is_terminal) or bool(self._display._is_quiet):
            return
        # A redirected stream receives only operator-actionable status changes.
        # Phase-only footer updates are transient presentation state and must not
        # create a line on a force-terminal StringIO console; an attention state
        # is a durable transition an operator needs to review after the run.
        model = self._model
        if model is None or model.attention is None:
            return
        renderable = self._renderable()
        if renderable.plain == self._durable_frame:
            return
        self._ctx().console.print(renderable)
        self._durable_frame = renderable.plain

    def _fallback_cleanup(self) -> None:
        if not self._fallback_rendered:
            return
        self._fallback_rendered = False
        self._fallback_frame = None
        file_obj: IO[str] = self._ctx().console.file
        file_obj.write("\r\x1b[1A\x1b[2K")
        file_obj.flush()

    def _refresh_live_if_changed(self) -> bool:
        """Refresh the interactive footer only when its visible frame changed."""
        with self._lock:
            live = self._live
            if live is None:
                return False
            frame = self._renderable().plain
            if frame == self._last_live_frame:
                return False
            self._last_live_frame = frame
            with contextlib.suppress(Exception):
                live.refresh()
                self._live_frame_rendered = True
                return True
        return False

    def _run_ticker(self, stop: threading.Event) -> None:
        """Poll the live frame at the bounded status-bar cadence."""
        while not stop.wait(1 / _STATUS_BAR_REFRESH_PER_SECOND):
            self._refresh_live_if_changed()

    def _start_ticker(self) -> None:
        stop = threading.Event()
        ticker = threading.Thread(
            target=self._run_ticker,
            args=(stop,),
            name="ralph-status-bar",
            daemon=True,
        )
        self._ticker_stop = stop
        self._ticker = ticker
        ticker.start()

    def _stop_ticker(self) -> None:
        stop = self._ticker_stop
        ticker = self._ticker
        self._ticker_stop = None
        self._ticker = None
        if stop is not None:
            stop.set()
        if ticker is not None and ticker is not threading.current_thread():
            ticker.join(timeout=1.0)

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
        self._started = True
        if not self._gate():
            return
        with contextlib.suppress(Exception):
            from rich.live import Live

            renderable = self._renderable()
            live = Live(
                renderable,
                console=self._ctx().console,
                transient=_STATUS_BAR_TRANSIENT,
                auto_refresh=False,
                screen=False,
                get_renderable=self._renderable,
            )
            live.start()
            self._live = live
            self._last_live_frame = renderable.plain
            with contextlib.suppress(Exception):
                live.refresh()
                self._live_frame_rendered = True
            self._start_ticker()
            self._fallback_render_once()

    def stop(self) -> None:
        """Tear down the Live region. Idempotent and safe to call without :meth:`start`."""
        live = self._live
        if live is None:
            return
        self._live = None
        self._last_live_frame = None
        self._stop_ticker()
        if self._live_frame_rendered:
            with contextlib.suppress(Exception):
                live.update(Text(" "), refresh=False)
        with contextlib.suppress(Exception):
            live.stop()
        self._live_frame_rendered = False
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
            return
        self._emit_durable_transition_if_changed()
