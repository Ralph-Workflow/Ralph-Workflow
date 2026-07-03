"""Display helpers for CLI output.

These exports cover progress rendering, phase/status display, and simple table
views used by CLI diagnostics and listing commands.

.. important:: Display Architecture and DI Contract

   **Single source of truth:** ``DisplayContext`` is the only permitted source
   of ``Console``, ``Theme``, terminal width, color policy, display mode, and
   adaptive character limits. No renderer may construct its own ``rich.Console``.

   **Single display owner:** ``ParallelDisplay`` is the single source of truth
   for all user-facing display logic in Ralph Workflow. All 42 consolidated
   ``emit_*`` methods (41 instance methods on ``ParallelDisplay`` plus the
   module-level ``emit_activity_line``) own every banner, table, panel, and
   one-shot status surface. The legacy ``ralph.display.phase_banner``,
   ``ralph.display.artifact_renderer``, ``ralph.display.first_run_panel``,
   ``ralph.display.tables``, ``ralph.banner``, and ``ralph.cli.options``
   modules have been deleted. The persistent bottom Status Bar is composed
   via the ``ralph.display.status_bar`` module: ``StatusBar`` (a lifecycle
   class reachable as ``ParallelDisplay.status_bar``) composes the Live
   region, and the pure free function
   ``ralph.display.status_bar.render_status_bar(model, ctx, *, home=None)``
   owns the layout / color / spacing / alignment / truncation logic (its
   pure-function shape is what makes the layout testable in isolation). The
   single push-side surface is ``ParallelDisplay.update_status_bar(model)``;
   ``StatusBar.update(model)`` stores the model and the persistent footer
   is rendered on the
   ``ralph.display.status_bar._STATUS_BAR_REFRESH_PER_SECOND = 4.0`` Hz
   cadence (i.e. no eager ``live.refresh()`` from update). The Status Bar
   is the single owner of the run-level footer (working directory, active
   phase, applicable cycle counts) on real-TTY runs, gated on
   ``ctx.console.is_terminal AND ctx.console.file.isatty()`` to stay out of
   non-interactive output.

   **DI requirement:** Every public emit method on ``ParallelDisplay`` is
   reachable through a ``DisplayContext``; callers resolve an active display
   via ``resolve_active_display(display_context)`` and call
   ``display.emit_*``. There are no silent ``Console``-only fallbacks in
   production code. Callers must construct a ``DisplayContext`` via
   ``make_display_context()`` before invoking renderers.

   **Invariant enforcement:** ``tests/display/test_di_invariants.py`` scans
   every file under ``ralph/display/`` to assert that ``Console(`` and
   ``Theme(`` only appear in ``theme.py``, and that
   ``os.environ``/``os.getenv`` only appear in ``context.py`` and
   ``content_condenser.py``. The companion
   ``tests/display/test_single_mode_anti_drift.py`` AST-scans
   ``ralph/display/`` to assert that no future commit re-introduces a
   compact / medium / wide branch (single ``default`` mode is the only
   owner of display layout).

   **Display mode (single default):** After the wt-028-display consolidation,
   ``DisplayContext.mode`` is always the literal string ``"default"``. There
   is no width-based dispatch, no ``compact`` / ``medium`` / ``wide`` tier,
   and no per-mode limits table. The historical ``RALPH_FORCE_NARROW``
   env var is silently ignored. The persistent bottom Status Bar always
   renders all applicable fields (working directory, active phase,
   applicable outer development iteration, applicable inner analysis
   iteration) for any applicable terminal width (>= 14 cols) — at
   very narrow widths the bar drops the phase marker and per-iteration
   glyphs so both iteration labels remain visible, and only the
   long-path middle-truncation and long-phase tail-truncation
   budgets adapt to width.

   **Environment variable precedence (highest to lowest):**

   - ``force_width`` argument to ``make_display_context()`` — overrides
     terminal width detection.
   - ``COLUMNS`` (positive integer) — overrides the console's auto-detected
     width.
   - ``console.width`` — the default fallback from Rich's terminal detection.

   **Color environment variables:**

   - ``NO_COLOR`` (any value) — disables all color output.  Takes precedence
     over ``FORCE_COLOR``.
   - ``FORCE_COLOR`` (any value) — enables color output on non-TTY streams.

   **Glyph environment variables:**

   - ``RALPH_FORCE_ASCII`` (``1``/``true``/``yes``/``on``) — disables Unicode
     glyphs; renderers use ASCII fallbacks (e.g. ``->`` instead of ``→``).
   - ``TERM=dumb`` — disables Unicode glyphs via the same fallback path.

   **Streaming environment variables:**

   - ``RALPH_STREAMING_DEDUP`` (``0``/``false``/``no``/``off``) — disables
     consecutive-fragment deduplication in streaming blocks.
   - ``RALPH_STREAMING_CHECKPOINTS`` (``0``/``false``/``no``/``off``) —
     disables periodic checkpoint lines during long streaming blocks.

   **Long-content environment variables:**

   - ``RALPH_LONG_CONTENT_SUMMARY`` (``0``/``false``/``no``/``off``) —
     disables fallback-headline generation for long content blocks
     (handled in ``content_condenser.py``).
   - ``RALPH_LONG_CONTENT_AI_SUMMARY`` (``0``/``false``/``no``/``off``) —
     disables AI-based headline generation for long content blocks.

   **Width refresh (cross-platform):** The runner installs a width refresher
   via ``install_width_refresher()`` at pipeline start. On POSIX this uses a
   SIGWINCH signal handler; on Windows or non-main threads it falls back to a
   poll-based daemon thread. Either path calls ``DisplayContext.refreshed()``
   which re-reads the current terminal width and recomputes adaptive limits
   while keeping the mode at ``"default"``. Renderers that buffer adaptive
   limits (e.g. ``PlainLogRenderer``) call ``refreshed()`` at phase
   boundaries via ``flush_blocks()`` to pick up new sizes. The runner also
   keeps its live display object and nested plain renderer synced with the
   refreshed context so later banners and summaries use the new limits.
   The returned stop callback is invoked on shutdown to clean up any poll
   thread.
"""

from ralph.display._run_start_orientation import RunStartOrientation
from ralph.display.context import (
    DisplayContext,
    install_sigwinch_refresher,
    install_width_refresher,
    make_display_context,
)
from ralph.display.parallel_display import (
    ParallelDisplay,
    build_default_display_legacy_bridge,
    emit_activity_line,
    get_display_context,
    phase_style_for_phase,
    resolve_active_display,
    resolve_display,
    status_text,
    strip_markup,
    subscriber_for_display,
)
from ralph.display.phase_status import (
    PhaseIterationContext,
    format_analysis_cycle,
    format_dev_cycle,
)
from ralph.display.status_bar import (
    StatusBar,
    StatusBarModel,
    render_status_bar,
)

__all__ = [
    "DisplayContext",
    "ParallelDisplay",
    "PhaseIterationContext",
    "RunStartOrientation",
    "StatusBar",
    "StatusBarModel",
    "build_default_display_legacy_bridge",
    "emit_activity_line",
    "format_analysis_cycle",
    "format_dev_cycle",
    "get_display_context",
    "install_sigwinch_refresher",
    "install_width_refresher",
    "make_display_context",
    "phase_style_for_phase",
    "render_status_bar",
    "resolve_active_display",
    "resolve_display",
    "status_text",
    "strip_markup",
    "subscriber_for_display",
]
