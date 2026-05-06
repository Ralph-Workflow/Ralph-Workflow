"""Display helpers for CLI output.

These exports cover progress rendering, phase/status display, and simple table
views used by CLI diagnostics and listing commands.

.. important:: Display Architecture and DI Contract

   **Single source of truth:** ``DisplayContext`` is the only permitted source
   of ``Console``, ``Theme``, terminal width, color policy, display mode, and
   adaptive character limits. No renderer may construct its own ``rich.Console``.

   **DI requirement:** Every public renderer function requires a
   ``display_context: DisplayContext`` argument. There are no silent
   ``Console``-only fallbacks. Callers must construct a ``DisplayContext``
   via ``make_display_context()`` before invoking renderers.

   **Invariant enforcement:** ``tests/display/test_di_invariants.py`` scans
   every file under ``ralph/display/`` and ``ralph/banner.py`` to assert that
   ``Console(`` and ``Theme(`` only appear in ``theme.py``, and that
   ``os.environ``/``os.getenv`` only appear in ``context.py`` and
   ``content_condenser.py``.

   **Environment variable precedence (highest to lowest):**

   - ``RALPH_FORCE_NARROW`` (``1``/``true``/``yes``/``on``) — forces
     ``compact`` mode regardless of terminal width.
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

   **Mode thresholds:**

   - ``compact`` — terminal width < 60 columns.
   - ``medium`` — terminal width 60-99 columns.
   - ``wide`` — terminal width ≥ 100 columns.

   **SIGWINCH refresh (POSIX):** On non-Windows platforms, a SIGWINCH signal
   handler is installed via ``install_sigwinch_refresher()`` at pipeline start.
   The handler calls ``DisplayContext.refreshed()`` which re-reads the current
   terminal width and recomputes mode and adaptive limits. Renderers that
   buffer adaptive limits (e.g. ``PlainLogRenderer``) call ``refreshed()`` at
   phase boundaries via ``flush_blocks()`` to pick up new sizes. The runner
   also keeps its live display object and nested plain renderer synced with
   the refreshed context so later banners and summaries use the new mode.

   **Compact mode:** When ``ctx.mode == 'compact'``, renderers suppress
   secondary columns, extra blank lines, and descriptive rules to fit
   narrow terminals.
"""

from ralph.display.artifact_renderer import (
    render_analysis_decision,
    render_commit_message,
    render_fix_artifact,
    render_missing_plan_hint,
    render_plan_artifact,
)
from ralph.display.context import DisplayContext, install_sigwinch_refresher, make_display_context
from ralph.display.phase_banner import (
    show_phase_close_banner,
    show_phase_start,
    show_phase_start_from_entry,
    show_phase_transition,
)
from ralph.display.phase_status import (
    PhaseIterationContext,
    format_analysis_cycle,
    format_dev_cycle,
)
from ralph.display.plain_renderer import RunStartOrientation
from ralph.display.progress import RalphProgress, get_progress
from ralph.display.tables import show_agents, show_config, show_providers

__all__ = [
    "DisplayContext",
    "PhaseIterationContext",
    "RalphProgress",
    "RunStartOrientation",
    "format_analysis_cycle",
    "format_dev_cycle",
    "get_progress",
    "install_sigwinch_refresher",
    "make_display_context",
    "render_analysis_decision",
    "render_commit_message",
    "render_fix_artifact",
    "render_missing_plan_hint",
    "render_plan_artifact",
    "show_agents",
    "show_config",
    "show_phase_close_banner",
    "show_phase_start",
    "show_phase_start_from_entry",
    "show_phase_transition",
    "show_providers",
]
