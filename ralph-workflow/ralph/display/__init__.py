"""Display helpers for CLI output.

These exports cover progress rendering, phase/status display, and simple table
views used by CLI diagnostics and listing commands.

.. important::
   Renderers in this package MUST NOT construct their own ``rich.Console``.
   All rendering depends on a ``DisplayContext`` that is constructed once via
   ``make_display_context()`` and threaded through the call graph. When a
   renderer receives ``display_context=None``, it creates a fresh context via
   ``make_display_context()`` whose mode is determined by the terminal
   environment (via ``RALPH_FORCE_NARROW``, ``RALPH_FORCE_WIDE``, or actual
   terminal width detection). Compact mode column suppression is only applied
   when an explicit ``display_context`` with ``mode=='compact'`` is passed.
"""

from ralph.display.artifact_renderer import (
    render_analysis_decision,
    render_commit_message,
    render_fix_artifact,
    render_missing_plan_hint,
    render_plan_artifact,
)
from ralph.display.context import DisplayContext, make_display_context
from ralph.display.phase_banner import (
    PhaseStartContext,
    show_phase_complete,
    show_phase_start,
    show_phase_start_from_state,
    show_phase_transition,
)
from ralph.display.plain_renderer import RunStartOrientation
from ralph.display.progress import RalphProgress, get_progress
from ralph.display.status import display_phase, display_progress
from ralph.display.tables import show_agents, show_config, show_providers

__all__ = [
    "DisplayContext",
    "PhaseStartContext",
    "RalphProgress",
    "RunStartOrientation",
    "display_phase",
    "display_progress",
    "get_progress",
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
