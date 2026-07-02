"""Persistent Status Bar at the bottom of the interactive terminal display.

The Status Bar shows working directory, active phase, and any applicable outer
development iteration and inner analysis iteration during interactive runs. It
is the single owner of run-level layout, color, spacing, truncation, and
live-update behavior; the per-unit ``emit_status_line`` and the transient
``waiting_status_line`` are orthogonal surfaces left intact for one-shot
transcript lines.

The bar is gated on a real-TTY check (``console.is_terminal AND
console.file.isatty()``) so it stays out of non-interactive runs (redirects,
pipes, CI logs, StringIO test consoles, and force_terminal+StringIO consoles).

DI / purity invariants:

- ``render_status_bar`` is a pure function: no I/O, no env reads, no Console
  construction, no ``Path.home()`` calls (``home`` is a parameter so the
  function can be tested deterministically).
- ``status_bar.py`` does not construct a ``rich.Console`` and does not read
  ``os.environ`` / ``os.getenv``; the DI invariants test asserts this.
- The StatusBar lifecycle class lazily constructs a single ``rich.live.Live``
  region only when the real-TTY gate passes; it never reads env at module
  import.

Cadence constants:

- ``_STATUS_BAR_REFRESH_PER_SECOND`` (default ``4.0``): refresh rate for the
  Live region. Pinned by ``test_status_bar_pins_steady_cadence_config``.
- ``_STATUS_BAR_TRANSIENT`` (default ``True``): frames are erased on stop,
  preserving clean scrollback, copy/paste, terminal search, and post-run
  log review.
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


_PATH_BUDGET_BY_MODE: dict[str, int] = {
    "compact": 20,
    "medium": 32,
    "wide": 48,
}
_PHASE_LABEL_BUDGET_BY_MODE: dict[str, int] = {
    "compact": 16,
    "medium": 22,
    "wide": 28,
}
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

    The first 8 characters and the final path segment are preserved when the
    path is too long. If the path fits in ``budget`` it is returned unchanged.
    """
    if len(path) <= budget:
        return path
    if budget <= _MIN_BUDGET:
        return path[:budget]
    last_sep = path.rfind(os.sep)
    last_segment = path[last_sep + 1:] if last_sep >= 0 else path
    prefix_budget = max(1, budget - _ELLIPSIS_LEN - len(last_segment))
    if prefix_budget >= len(path) - len(last_segment) - 1:
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


def _ascii_separator() -> str:
    """Return the ASCII fallback separator between status bar fields."""
    return " | "


def render_status_bar(
    model: StatusBarModel,
    ctx: DisplayContext,
    *,
    home: str | None = None,
) -> Text:
    """Render the single-line Status Bar footer for the given model.

    This function is PURE: no I/O, no env reads, no Console construction,
    no ``Path.home()`` calls. ``home`` is a parameter so callers can supply
    the resolved home directory once (the ``StatusBar`` lifecycle resolves
    it at construction; tests pass an explicit value).

    Args:
        model: Immutable view-model describing the bar contents.
        ctx: Display context providing mode, glyphs, and theme-aware style.
        home: Optional home directory; when supplied and ``model.workspace_root``
            starts with it, the rendered path is home-relative.

    Returns:
        A single-line ``rich.text.Text`` carrying the bar contents. The rendered
        text never contains ``\\n`` so the bar cannot wrap into the working area.
    """
    mode = ctx.mode
    path_budget = _PATH_BUDGET_BY_MODE[mode]
    label_budget = _PHASE_LABEL_BUDGET_BY_MODE[mode]
    separator = _ascii_separator()
    path_display = _home_relative(model.workspace_root, home)
    path_display = _middle_truncate_path(path_display, path_budget)
    phase_display = _tail_truncate(model.phase_label, label_budget)
    text = Text()
    if mode != "compact":
        marker = ctx.glyph_for("phase_marker")
        text.append(marker + " ", style="theme.status.bar_marker")
    text.append(phase_display, style=model.phase_style)
    text.append(separator)
    path_marker = ctx.glyph_for("milestone")
    text.append(path_marker + " ", style="theme.status.path_marker")
    text.append(path_display, style="theme.status.path")
    if mode == "compact":
        return text
    if model.outer_dev_iteration is not None:
        text.append(separator)
        text.append(ctx.glyph_for("outer_dev") + " ", style="theme.outer_dev")
        text.append(format_dev_cycle(model.outer_dev_iteration, model.outer_dev_cap))
    if mode == "medium":
        return text
    if model.inner_analysis is not None:
        text.append(separator)
        text.append(ctx.glyph_for("inner_analysis") + " ", style="theme.inner_analysis")
        text.append(format_analysis_cycle(model.inner_analysis, model.inner_analysis_cap))
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
        """Store ``model`` and force an immediate Live refresh.

        Safe to call before :meth:`start` (the model is stored and the
        next :meth:`start` constructs the Live region with the latest
        renderable). Thread-safe under :attr:`_lock`. When the Live
        region is active, an explicit ``refresh()`` is invoked so the
        new model appears in the rendered output deterministically
        (without waiting for the next 4 Hz refresh tick).
        """
        with self._lock:
            self._model = model
        live = self._live
        if live is not None:
            with contextlib.suppress(Exception):
                live.refresh()
