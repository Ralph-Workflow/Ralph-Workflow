"""Persistent Status Bar at the bottom of the interactive terminal display.

The Status Bar shows working directory, active phase, and any applicable
outer development iteration and inner analysis iteration during
interactive runs. It is the single owner of run-level layout, color,
spacing, truncation, and live-update behavior; the per-unit
``emit_status_line`` and the transient ``waiting_status_line`` are
orthogonal surfaces left intact for one-shot transcript lines.

After the wt-028-display consolidation, Ralph Workflow exposes exactly
ONE display mode (``default``). The persistent Status Bar always renders
all applicable fields:

- working directory (middle-truncated when long),
- active phase label (tail-truncated when long),
- outer development iteration (when non-``None``),
- inner analysis iteration (when non-``None``).

Only the path middle-truncation budget and the phase tail-truncation
budget adapt to terminal width.

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

# Canonical label widths (full form: ``Dev 1/3`` / ``Analysis 2/5``).
_OUTER_DEV_LABEL_MAX_CHARS: int = 13
_INNER_ANALYSIS_LABEL_MAX_CHARS: int = 17
# Compact label widths (D1/3 / A2/5).
_OUTER_DEV_LABEL_COMPACT_MAX_CHARS: int = 4
_INNER_ANALYSIS_LABEL_COMPACT_MAX_CHARS: int = 4
# Minimal label widths (1/3 / 2/5; no prefix).
_OUTER_DEV_LABEL_MINIMAL_MAX_CHARS: int = 4
_INNER_ANALYSIS_LABEL_MINIMAL_MAX_CHARS: int = 4
# Threshold at and above which the canonical (full) label form is honored.
_WIDE_DEFAULT_BUDGET_THRESHOLD: int = 120


@dataclass(frozen=True)
class StatusBarModel:
    """Immutable view-model for the persistent Status Bar footer.

    Attributes:
        workspace_root: Working-directory path to display.
        phase_label: Human-readable phase label (e.g. ``Development``).
        phase_style: Rich style string applied to the phase label
            (e.g. ``theme.phase.development``); also carries textual
            meaning so the bar is readable when color is disabled.
        outer_dev_iteration: Current outer development cycle (1-indexed),
            or ``None`` when the active phase does not track outer progress.
        outer_dev_cap: Outer development cap, or ``None`` when unknown.
        inner_analysis: Current inner analysis iteration (1-indexed),
            or ``None`` when the active phase does not track analysis cycles.
        inner_analysis_cap: Inner analysis iteration cap, or ``None`` when
            unknown.
    """

    workspace_root: str
    phase_label: str
    phase_style: str
    outer_dev_iteration: int | None = None
    outer_dev_cap: int | None = None
    inner_analysis: int | None = None
    inner_analysis_cap: int | None = None


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

    The single default-mode Status Bar always renders phase + dir +
    (any applicable outer_dev) + (any applicable inner_analysis).
    At very narrow widths the iteration labels degrade from canonical
    (``Dev 1/3`` / ``Analysis 2/5``) through compact (``D1/3`` /
    ``A2/5``) to minimal (``1/3`` / ``2/5``) forms so the bar fits
    the terminal width without dropping the iteration fields.

    The phase and path budgets adapt to whatever space remains after
    the iteration segments are sized, so the rendered text always
    fits ``ctx.width`` (no wrap, no overflow). At very narrow widths
    the bar drops the phase_marker and the per-iteration glyphs
    (``render_marker=False``, ``render_iter_glyph=False``) to keep
    both iteration labels visible: a 14-col bar may render as
    ``1/3 2/5`` instead of the canonical
    ``■ Dev 1/3 ◆ ◎ Analysis 2/5`` at 100+ cols.
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

    Iteration segments are ALWAYS present (in canonical / compact /
    minimal form) when the model fields are non-``None``. The function
    picks the most descriptive layout that fits ``ctx.width``:

    1. At widths ``>= _WIDE_DEFAULT_BUDGET_THRESHOLD`` the canonical
       ``Dev N/cap`` / ``Analysis N/cap`` labels render in full with
       the marker and per-iteration glyphs.
    2. Below the wide threshold, the compact form (``D1/3`` / ``A2/5``)
       is used when it fits.
    3. Below the compact threshold, the minimal form (``1/3`` / ``2/5``)
       is used when it fits.
    4. Below the minimal-with-marker threshold, the phase_marker is
       dropped (``render_marker=False``) to recover two characters.
    5. Below the no-marker threshold, the per-iteration glyphs are
       dropped (``render_iter_glyph=False``) so the labels still fit
       alongside phase + path at very narrow widths.

    The phase and path budgets adapt to whatever space remains after
    the iteration segments are sized; they may be ``0`` at very narrow
    widths. The rendered text always fits ``ctx.width`` (no wrap, no
    overflow), and the iteration labels are ALWAYS present when the
    model fields are non-``None``.

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

    def _iter_overhead(outer_label: int, inner_label: int, with_glyph: bool) -> int:
        """Per-iteration overhead (leading separator + glyph + space + label).

        Each iteration segment renders as ``separator + [glyph + " "] + label``.
        The leading separator is included here so ``_base_overhead`` does
        not double-count the trailing separator.
        """
        total = 0
        if has_outer_dev:
            total += separator_len + outer_label
            if with_glyph:
                total += outer_dev_glyph_len + 1
        if has_inner_analysis:
            total += separator_len + inner_label
            if with_glyph:
                total += inner_analysis_glyph_len + 1
        return total

    def _base_overhead(with_marker: bool) -> int:
        """Marker + phase|path separator only (no trailing iter separator)."""
        ml = marker_len if with_marker else 0
        return ml + separator_len

    def _total_width(
        outer_label: int,
        inner_label: int,
        with_marker: bool,
        with_glyph: bool,
        phase_budget: int,
        path_budget: int,
    ) -> int:
        return (
            _base_overhead(with_marker)
            + _iter_overhead(outer_label, inner_label, with_glyph)
            + phase_budget
            + path_budget
        )

    def _distribute(
        outer_label: int,
        inner_label: int,
        with_marker: bool,
        with_glyph: bool,
    ) -> _FieldBudgets:
        """Build _FieldBudgets sized so the iteration segments fit alongside phase + path."""
        remaining = (
            ctx.width
            - _base_overhead(with_marker)
            - _iter_overhead(outer_label, inner_label, with_glyph)
        )
        if remaining <= 0:
            return _FieldBudgets(
                0, 0, outer_label, inner_label, with_marker, with_glyph
            )
        phase_budget = min(DEFAULT_PHASE_LABEL_BUDGET, remaining // 2)
        path_budget = remaining - phase_budget
        return _FieldBudgets(
            phase_budget, path_budget, outer_label, inner_label, with_marker, with_glyph
        )

    label_forms: tuple[tuple[int, int], ...] = (
        (_OUTER_DEV_LABEL_MAX_CHARS, _INNER_ANALYSIS_LABEL_MAX_CHARS),
        (_OUTER_DEV_LABEL_COMPACT_MAX_CHARS, _INNER_ANALYSIS_LABEL_COMPACT_MAX_CHARS),
        (_OUTER_DEV_LABEL_MINIMAL_MAX_CHARS, _INNER_ANALYSIS_LABEL_MINIMAL_MAX_CHARS),
    )

    for with_marker in (True, False):
        for with_glyph in (True, False):
            for outer_label, inner_label in label_forms:
                budget = _distribute(
                    outer_label, inner_label, with_marker, with_glyph
                )
                if (
                    _total_width(
                        outer_label,
                        inner_label,
                        with_marker,
                        with_glyph,
                        budget.phase_budget,
                        budget.path_budget,
                    )
                    <= ctx.width
                ):
                    return budget

    return _FieldBudgets(
        0,
        0,
        _OUTER_DEV_LABEL_MINIMAL_MAX_CHARS,
        _INNER_ANALYSIS_LABEL_MINIMAL_MAX_CHARS,
        False,
        False,
    )


def _format_dev_label(n: int, cap: int | None, max_chars: int) -> str:
    """Format the outer_dev label using the form that fits ``max_chars``."""
    if max_chars <= 0:
        return ""
    if max_chars >= _OUTER_DEV_LABEL_MAX_CHARS:
        return format_dev_cycle(n, cap)
    if max_chars >= _OUTER_DEV_LABEL_COMPACT_MAX_CHARS:
        return format_dev_cycle_compact(n, cap)
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

    The single default-mode layout ALWAYS renders phase + dir + (any
    applicable outer_dev) + (any applicable inner_analysis). When
    ``ctx.width`` is too narrow to fit the canonical forms (``Dev 1/3``
    / ``Analysis 2/5``) the labels degrade through compact
    (``D1/3`` / ``A2/5``) and minimal (``1/3`` / ``2/5``) forms, and
    finally drop an iteration segment only as a last resort so the
    bar still fits ``ctx.width``.

    The phase and path labels are tail/middle truncated to fit the
    remaining budget. ``len(text.plain) <= ctx.width`` always holds,
    and the rendered text never contains a newline.

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
        bar fits any terminal width.
    """
    separator = _field_separator(ctx)
    path_display = _home_relative(model.workspace_root, home)
    phase_display = model.phase_label

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
    return text


class StatusBar:
    """Lifecycle owner for the persistent bottom Status Bar.

    The StatusBar is composed by :class:`ralph.display.parallel_display.ParallelDisplay`
    and reachable via ``pd.status_bar``. It exposes ``update(model)`` as the
    single push-side surface; reads happen via ``last_model``. The ``start()``
    and ``stop()`` methods are wired through ParallelDisplay's own
    ``start()`` / ``stop()`` lifecycle.

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

    __slots__ = ("_display", "_home", "_live", "_lock", "_model")

    def __init__(self, display: ParallelDisplay) -> None:
        self._display: _StatusBarHost = display
        self._home = str(pathlib.Path.home())
        self._model: StatusBarModel | None = None
        self._live: _Live | None = None
        self._lock = threading.Lock()

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

    def start(self) -> None:
        """Begin rendering the Status Bar inside a transient Rich Live region.

        No-op when the real-TTY gate is closed (non-tty console, redirected
        output, StringIO test console, quiet mode), or when a Live region
        is already active. Idempotent.

        The Live region is constructed with ``get_renderable=self._renderable``
        so each refresh tick re-reads the latest model — the initial
        ``renderable`` argument is only the first-frame content.
        """
        if not self._gate():
            return
        with contextlib.suppress(Exception):
            from rich.live import Live

            self._live = Live(
                self._renderable(),
                console=self._ctx().console,
                transient=_STATUS_BAR_TRANSIENT,
                refresh_per_second=_STATUS_BAR_REFRESH_PER_SECOND,
                screen=False,
                get_renderable=self._renderable,
            )
            self._live.start()

    def stop(self) -> None:
        """Tear down the Live region. Idempotent and safe to call without :meth:`start`."""
        live = self._live
        if live is None:
            return
        self._live = None
        with contextlib.suppress(Exception):
            live.stop()

    def update(self, model: StatusBarModel) -> None:
        """Store ``model`` for the Live region to pick up on its next refresh tick.

        The update itself is intentionally a pure store: it does NOT force
        an immediate ``live.refresh()``. The persistent footer is owned by
        the Live region's :data:`_STATUS_BAR_REFRESH_PER_SECOND` cadence
        (4.0 Hz / 250 ms by default), so update calls feed a fresh
        :class:`StatusBarModel` and the next refresh tick renders it. This
        keeps update cheap, deterministic, and free of any rendering
        side-effects; it also keeps Live's bounded refresh rate the single
        owner of refresh-side behavior, matching the design constraint that
        the StatusBar is a single owner of run-level live-update cadence.

        Safe to call before :meth:`start`; in that case the model is
        stored and the subsequent :meth:`start` constructs the Live region
        using the latest model as its initial renderable. Thread-safe
        under :attr:`_lock`.
        """
        with self._lock:
            self._model = model
