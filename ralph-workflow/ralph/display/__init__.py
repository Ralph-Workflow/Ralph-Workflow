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

   **Width and mode precedence (highest to lowest):**

   1. ``RALPH_FORCE_NARROW`` env var (set to ``1``/``true``/``yes``/``on``)
      forces ``compact`` mode regardless of terminal width.
   2. ``force_width`` argument to ``make_display_context()`` overrides everything.
   3. ``COLUMNS`` env var (positive integer) overrides the console's width.
   4. ``console.width`` is the default fallback.

   **Color precedence:** ``NO_COLOR`` env var (any value) disables color.
   ``FORCE_COLOR`` (any value) enables color. ``NO_COLOR`` takes precedence.

   **Mode thresholds:** ``compact`` (< 60 cols), ``medium`` (60-99 cols),
   ``wide`` (>= 100 cols).

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
    PhaseStartContext,
    show_phase_complete,
    show_phase_start,
    show_phase_start_from_state,
    show_phase_transition,
)
from ralph.display.plain_renderer import RunStartOrientation
from ralph.display.progress import RalphProgress, get_progress
from ralph.display.tables import show_agents, show_config, show_providers

__all__ = [
    "DisplayContext",
    "PhaseStartContext",
    "RalphProgress",
    "RunStartOrientation",
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
    "show_phase_complete",
    "show_phase_start",
    "show_phase_start_from_state",
    "show_phase_transition",
    "show_providers",
]
