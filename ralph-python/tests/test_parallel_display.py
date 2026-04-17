"""Tests for ParallelDisplay mode detection."""

from __future__ import annotations

from rich.console import Console

from ralph.display.parallel_display import NARROW_THRESHOLD, ParallelDisplay, detect_mode


def test_ci_env_forces_lines() -> None:
    console = Console(force_terminal=True, width=120)
    assert detect_mode(console, {"CI": "1"}) == "lines"


def test_ci_empty_string_does_not_force_lines() -> None:
    """CI='' is falsy — should not trigger lines mode on its own."""
    console = Console(force_terminal=True, width=120)
    assert detect_mode(console, {"CI": ""}) == "dashboard"


def test_no_color_forces_lines() -> None:
    console = Console(force_terminal=True, width=120)
    assert detect_mode(console, {"NO_COLOR": "1"}) == "lines"


def test_no_color_empty_string_forces_lines() -> None:
    """NO_COLOR='' — merely being set (even empty) triggers lines mode."""
    console = Console(force_terminal=True, width=120)
    assert detect_mode(console, {"NO_COLOR": ""}) == "lines"


def test_term_dumb_forces_lines() -> None:
    console = Console(force_terminal=True, width=120)
    assert detect_mode(console, {"TERM": "dumb"}) == "lines"


def test_term_other_value_does_not_force_lines() -> None:
    console = Console(force_terminal=True, width=120)
    assert detect_mode(console, {"TERM": "xterm-256color"}) == "dashboard"


def test_non_terminal_console_forces_lines() -> None:
    console = Console(force_terminal=False, width=120)
    assert detect_mode(console, {}) == "lines"


def test_narrow_terminal_forces_lines() -> None:
    console = Console(force_terminal=True, width=40)
    assert detect_mode(console, {}) == "lines"


def test_threshold_boundary_forces_lines() -> None:
    """Width exactly at NARROW_THRESHOLD is still too narrow."""
    console = Console(force_terminal=True, width=NARROW_THRESHOLD)
    assert detect_mode(console, {}) == "lines"


def test_threshold_plus_one_is_dashboard() -> None:
    console = Console(force_terminal=True, width=NARROW_THRESHOLD + 1)
    assert detect_mode(console, {}) == "dashboard"


def test_dashboard_mode_when_all_good() -> None:
    console = Console(force_terminal=True, width=120)
    assert detect_mode(console, {}) == "dashboard"


def test_parallel_display_mode_detected_at_init() -> None:
    console = Console(force_terminal=True, width=120)
    pd = ParallelDisplay(console, {})
    assert pd.mode == "dashboard"


def test_parallel_display_mode_lines_when_ci() -> None:
    console = Console(force_terminal=True, width=120)
    pd = ParallelDisplay(console, {"CI": "1"})
    assert pd.mode == "lines"


def test_parallel_display_default_env_uses_os_environ() -> None:
    """Passing env=None should not crash (uses os.environ internally)."""
    console = Console(force_terminal=True, width=120)
    pd = ParallelDisplay(console)
    assert pd.mode in ("dashboard", "lines")


def test_parallel_display_mode_frozen_after_init() -> None:
    """mode attribute must not be writable."""
    console = Console(force_terminal=True, width=120)
    pd = ParallelDisplay(console, {})
    try:
        pd.mode = "lines"  # type: ignore[misc]
        raise AssertionError("Should have raised AttributeError")
    except AttributeError:
        pass


def test_parallel_display_context_manager() -> None:
    """__enter__/__exit__ must not raise."""
    console = Console(force_terminal=True, width=120)
    with ParallelDisplay(console, {}) as pd:
        assert pd.mode in ("dashboard", "lines")


def test_parallel_display_emit_stub_does_not_raise() -> None:
    console = Console(force_terminal=True, width=120)
    pd = ParallelDisplay(console, {})
    pd.emit("unit-1", "some output line")


def test_parallel_display_emit_none_unit_id_does_not_raise() -> None:
    console = Console(force_terminal=True, width=120)
    pd = ParallelDisplay(console, {})
    pd.emit(None, "some output line")


def test_parallel_display_set_status_stub_does_not_raise() -> None:
    from ralph.pipeline.worker_state import WorkerStatus

    console = Console(force_terminal=True, width=120)
    pd = ParallelDisplay(console, {})
    pd.set_status("unit-1", WorkerStatus.RUNNING)


def test_parallel_display_start_stop_stubs_do_not_raise() -> None:
    console = Console(force_terminal=True, width=120)
    pd = ParallelDisplay(console, {})
    pd.start()
    pd.stop()
