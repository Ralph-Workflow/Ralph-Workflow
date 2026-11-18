from collections.abc import Callable


def _load_display_context() -> object:
    from ralph.display.context import DisplayContext

    return DisplayContext


def _load_install_sigwinch_refresher() -> object:
    from ralph.display.context import install_sigwinch_refresher

    return install_sigwinch_refresher


def _load_install_width_refresher() -> object:
    from ralph.display.context import install_width_refresher

    return install_width_refresher


def _load_make_display_context() -> object:
    from ralph.display.context import make_display_context

    return make_display_context


def _load_phase_iteration_context() -> object:
    from ralph.display.phase_status import PhaseIterationContext

    return PhaseIterationContext


def _load_format_analysis_cycle() -> object:
    from ralph.display.phase_status import format_analysis_cycle

    return format_analysis_cycle


def _load_format_dev_cycle() -> object:
    from ralph.display.phase_status import format_dev_cycle

    return format_dev_cycle


def _load_status_bar() -> object:
    from ralph.display.status_bar import StatusBar

    return StatusBar


def _load_status_bar_model() -> object:
    from ralph.display.status_bar import StatusBarModel

    return StatusBarModel


def _load_render_status_bar() -> object:
    from ralph.display.status_bar import render_status_bar

    return render_status_bar


def _load_run_start_orientation() -> object:
    from ralph.display._run_start_orientation import RunStartOrientation

    return RunStartOrientation


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
    "DisplayContext": _load_display_context,
    "ParallelDisplay": _load_parallel_display,
    "PhaseIterationContext": _load_phase_iteration_context,
    "RunStartOrientation": _load_run_start_orientation,
    "StatusBar": _load_status_bar,
    "StatusBarModel": _load_status_bar_model,
    "format_analysis_cycle": _load_format_analysis_cycle,
    "format_dev_cycle": _load_format_dev_cycle,
    "install_sigwinch_refresher": _load_install_sigwinch_refresher,
    "install_width_refresher": _load_install_width_refresher,
    "make_display_context": _load_make_display_context,
    "render_status_bar": _load_render_status_bar,
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
