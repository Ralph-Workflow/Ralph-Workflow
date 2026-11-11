
from ralph.display._run_start_orientation import RunStartOrientation
from ralph.display.context import (
    DisplayContext,
    install_sigwinch_refresher,
    install_width_refresher,
    make_display_context,
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

_PARALLEL_DISPLAY_EXPORTS = frozenset(
    {
        "ParallelDisplay",
        "build_default_display_legacy_bridge",
        "emit_activity_line",
        "get_display_context",
        "phase_style_for_phase",
        "resolve_active_display",
        "resolve_display",
        "status_text",
        "strip_markup",
        "subscriber_for_display",
    }
)


def __getattr__(name: str) -> object:
    if name in _PARALLEL_DISPLAY_EXPORTS:
        from typing import cast

        from ralph.display import parallel_display

        return cast("object", getattr(parallel_display, name))
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(__all__)


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
