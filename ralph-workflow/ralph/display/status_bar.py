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

from ralph.display.phase_status import format_analysis_cycle, format_dev_cycle

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
    """
    if len(path) <= budget:
        return path
    last_sep = path.rfind(os.sep)
    last_segment = path[last_sep + 1:] if last_sep >= 0 else path
    last_segment_len = len(last_segment)
    min_two_part_budget = _ELLIPSIS_LEN + 1 + last_segment_len
    if budget <= _MIN_BUDGET:
        # Very tight budget: preserve the last segment (tail-truncated) so
        # the user can still identify the project from the trailing path
        # component. Tail-truncation adds ``...`` if it fits in the budget.
        if budget <= _ELLIPSIS_LEN:
            return last_segment[:budget]
        if budget < last_segment_len:
            return last_segment[: budget - _ELLIPSIS_LEN].rstrip() + "..."
        return last_segment
    prefix_budget = max(1, budget - min_two_part_budget)
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


_OUTER_DEV_LABEL_MAX_CHARS: int = 13
_INNER_ANALYSIS_LABEL_MAX_CHARS: int = 17
_WIDE_DEFAULT_BUDGET_THRESHOLD: int = 120


def _iteration_segment_widths(ctx: DisplayContext) -> tuple[int, int]:
    """Return the on-screen widths of the outer_dev and inner_analysis segments.

    Each segment is rendered as ``separator + glyph + ' ' + label``. The
    labels are bounded by ``_OUTER_DEV_LABEL_MAX_CHARS`` (max
    ``Dev #N`` / ``Dev N/cap`` form) and ``_INNER_ANALYSIS_LABEL_MAX_CHARS``
    (max ``Analysis #N`` / ``Analysis N/cap`` form). The returned
    widths are upper bounds so the path + phase budgets reserve enough
    room for the iteration segments at any width.
    """
    separator = _field_separator(ctx)
    outer_dev_glyph = ctx.glyph_for("outer_dev")
    inner_analysis_glyph = ctx.glyph_for("inner_analysis")
    outer_dev_segment = len(separator) + len(outer_dev_glyph) + 1 + _OUTER_DEV_LABEL_MAX_CHARS
    inner_analysis_segment = (
        len(separator) + len(inner_analysis_glyph) + 1 + _INNER_ANALYSIS_LABEL_MAX_CHARS
    )
    return outer_dev_segment, inner_analysis_segment


def _field_overhead(
    ctx: DisplayContext,
    *,
    has_outer_dev: bool,
    has_inner_analysis: bool,
) -> int:
    """Return the fixed per-segment overhead for the Status Bar's stable prefix.

    The persistent Status Bar always renders phase + dir + (any
    applicable outer_dev) + (any applicable inner_analysis). The
    fixed-width components are:

    - The phase marker (when glyphs are enabled): ``len(marker) + 1``.
    - One separator between phase and path: ``len(separator)``.
    - For each applicable iteration field, the segment overhead
      (separator + glyph + ' ' + max label width).

    The returned value reserves enough room that ``phase_budget +
    path_budget + overhead`` can never exceed ``ctx.width``. Path /
    phase truncation is the only mechanism that adapts to width; the
    iteration fields are NOT dropped.
    """
    separator = _field_separator(ctx)
    marker = ctx.glyph_for("phase_marker") + " " if ctx.glyphs_enabled else ""
    overhead = len(marker) + len(separator)
    if has_outer_dev or has_inner_analysis:
        outer_dev_segment, inner_analysis_segment = _iteration_segment_widths(ctx)
        if has_outer_dev:
            overhead += outer_dev_segment
        if has_inner_analysis:
            overhead += inner_analysis_segment
    return overhead


def _derive_field_budgets(width: int, overhead: int) -> tuple[int, int]:
    """Derive width-aware phase and path budgets from ``ctx.width``.

    Returns ``(phase_budget, path_budget)``.

    The phase and path budgets shrink with terminal width so the
    rendered text fits the terminal in a single line. After the
    wt-028-display consolidation the Status Bar always renders all
    applicable fields (phase + dir + outer_dev + inner_analysis) so
    the budgets must give way to make room for the iteration segments
    rather than dropping them. At wide widths (>=120 cols) the default
    budgets (phase=28, path=48) are honored. At narrow widths the
    budgets are scaled down proportionally so the entire bar still
    fits on a single line without wrapping into the working area.

    The total of the two budgets never exceeds ``width - overhead`` so
    ``phase_budget + path_budget + overhead <= width`` always holds.
    """
    remaining = width - overhead
    if remaining <= 0:
        return _MIN_BUDGET, _MIN_BUDGET
    if width >= _WIDE_DEFAULT_BUDGET_THRESHOLD:
        return DEFAULT_PHASE_LABEL_BUDGET, DEFAULT_PATH_BUDGET
    phase_budget = max(_MIN_BUDGET, min(DEFAULT_PHASE_LABEL_BUDGET, remaining // 2))
    path_budget = max(_MIN_BUDGET, remaining - phase_budget)
    return phase_budget, path_budget


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

    The single default-mode layout ALWAYS renders all applicable fields
    for the model, in order: phase, dir, outer_dev (when the model's
    ``outer_dev_iteration`` is not ``None``), inner_analysis (when the
    model's ``inner_analysis`` is not ``None``). NO field is dropped
    based on terminal width: only the long-path middle-truncation
    budget and the long-phase tail-truncation budget adapt to
    ``ctx.width``. At very narrow widths the rendered bar fits the
    terminal in a single line because the path and phase segments are
    truncated to derive smaller budgets from ``ctx.width``; the
    iteration segments (``Dev N/cap`` and ``Analysis N/cap``) are
    always rendered when applicable.

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
    overhead = _field_overhead(
        ctx,
        has_outer_dev=has_outer_dev,
        has_inner_analysis=has_inner_analysis,
    )
    phase_budget, path_budget = _derive_field_budgets(ctx.width, overhead)

    path_display = _middle_truncate_path(path_display, path_budget)
    phase_display = _tail_truncate(phase_display, phase_budget)
    text = Text()
    if ctx.glyphs_enabled:
        marker = ctx.glyph_for("phase_marker")
        text.append(marker + " ", style="theme.status.bar_marker")
    text.append(phase_display, style=model.phase_style)
    text.append(separator, style="theme.status.path_marker")
    text.append(path_display, style="theme.status.path")
    if has_outer_dev:
        text.append(separator, style="theme.status.path_marker")
        text.append(ctx.glyph_for("outer_dev") + " ", style="theme.outer_dev")
        text.append(
            format_dev_cycle(
                model.outer_dev_iteration or 0, model.outer_dev_cap
            )
        )
    if has_inner_analysis:
        text.append(separator, style="theme.status.path_marker")
        text.append(
            ctx.glyph_for("inner_analysis") + " ", style="theme.inner_analysis"
        )
        text.append(
            format_analysis_cycle(
                model.inner_analysis or 0, model.inner_analysis_cap
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
