from collections.abc import Callable

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


def _load_parallel_display() -> object:
    from ralph.display import parallel_display

    return parallel_display.ParallelDisplay


def _load_build_default_display_legacy_bridge() -> object:
    from ralph.display import parallel_display

    return parallel_display.build_default_display_legacy_bridge


def _load_emit_activity_line() -> object:
    from ralph.display import parallel_display

    return parallel_display.emit_activity_line


def _load_get_display_context() -> object:
    from ralph.display import parallel_display

    return parallel_display.get_display_context


def _load_phase_style_for_phase() -> object:
    from ralph.display import parallel_display

    return parallel_display.phase_style_for_phase


def _load_resolve_active_display() -> object:
    from ralph.display import parallel_display

    return parallel_display.resolve_active_display


def _load_resolve_display() -> object:
    from ralph.display import parallel_display

    return parallel_display.resolve_display


def _load_status_text() -> object:
    from ralph.display import parallel_display

    return parallel_display.status_text


def _load_strip_markup() -> object:
    from ralph.display import parallel_display

    return parallel_display.strip_markup


def _load_subscriber_for_display() -> object:
    from ralph.display import parallel_display

    return parallel_display.subscriber_for_display


_LOADERS: dict[str, Callable[[], object]] = {
    "ParallelDisplay": _load_parallel_display,
    "build_default_display_legacy_bridge": _load_build_default_display_legacy_bridge,
    "emit_activity_line": _load_emit_activity_line,
    "get_display_context": _load_get_display_context,
    "phase_style_for_phase": _load_phase_style_for_phase,
    "resolve_active_display": _load_resolve_active_display,
    "resolve_display": _load_resolve_display,
    "status_text": _load_status_text,
    "strip_markup": _load_strip_markup,
    "subscriber_for_display": _load_subscriber_for_display,
}


def __getattr__(name: str) -> object:
    loader = _LOADERS.get(name)
    if loader is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return loader()


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
