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
    if name not in _PARALLEL_DISPLAY_EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from ralph.display import parallel_display

    if name == "ParallelDisplay":
        return parallel_display.ParallelDisplay
    if name == "build_default_display_legacy_bridge":
        return parallel_display.build_default_display_legacy_bridge
    if name == "emit_activity_line":
        return parallel_display.emit_activity_line
    if name == "get_display_context":
        return parallel_display.get_display_context
    if name == "phase_style_for_phase":
        return parallel_display.phase_style_for_phase
    if name == "resolve_active_display":
        return parallel_display.resolve_active_display
    if name == "resolve_display":
        return parallel_display.resolve_display
    if name == "status_text":
        return parallel_display.status_text
    if name == "strip_markup":
        return parallel_display.strip_markup
    if name == "subscriber_for_display":
        return parallel_display.subscriber_for_display
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
